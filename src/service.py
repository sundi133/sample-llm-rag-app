import os
import uuid
import asyncio
import logging
import json
import os, sys
import requests

from dotenv import load_dotenv
from typing import List, Optional

from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
from fastapi.encoders import jsonable_encoder
from fastapi import status
from fastapi import (
    FastAPI,
    File,
    Form,
    UploadFile,
    WebSocket,
    HTTPException,
    Depends,
    Query,
    Request,
    Depends
)
from sqlalchemy.orm import Session
from sqlalchemy import select, create_engine, desc
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi import BackgroundTasks
from fastapi_sqlalchemy import DBSessionMiddleware, db
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from databases import Database
from datetime import datetime

from .models import Base, Dataset, QAData, LLMEndpoint
from .responses import DatasetResponse, QADataResponse, LLMEndpointResponse
from .ranking import evaluate_qa_data
from .generator import qa_generator
from .utils import (
    read_qa_data,
    read_endpoint_configurations,
    score_answer,
    get_llm_answer,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

app = FastAPI()
cwd = "/tmp"
upload_directory = os.path.join(cwd, "qa_generator_uploads")
output_directory = os.path.join(cwd, "qa_generator_outputs")

if not os.path.exists(upload_directory):
    os.makedirs(upload_directory)
if not os.path.exists(output_directory):
    os.makedirs(output_directory)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(BASE_DIR, ".env"))
sys.path.append(BASE_DIR)

app.add_middleware(DBSessionMiddleware, db_url=os.environ["POSTGRES_URL"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

POSTGRES_URL = os.environ["POSTGRES_URL"]
database = Database(POSTGRES_URL)
engine = create_engine(POSTGRES_URL)
Base.metadata.create_all(bind=engine)


def get_db():
    db = Session(autocommit=False, bind=engine)
    try:
        yield db
    finally:
        db.close()


def get_user_info(user_id:str):
    token = os.environ["CLERK_SECRET_KEY"]
    authorization = f'Bearer {token}'
    headers = {"Authorization": authorization}
    url = f'https://api.clerk.com/v1/users/{user_id}'
    response = requests.get(url, headers=headers)
    return response.status_code == 200


@app.get("/api")
def root():
    return {"status": "ok"}


@app.get("/api/health")
def health():
    return {"status": "pong"}


@app.post("/api/generate")
async def generator(
    background_tasks: BackgroundTasks,
    name: str = Form(default=""),
    userId: str = Form(default="-1"),
    orgId: str = Form(default="-1"),
    dataset_type: str = Form(default=""),
    chunk_size: int = Form(default=2000, ge=1, le=8000),
    persona: str = Form(default=""),
    behavior: str = Form(default=""),
    demographic: str = Form(default=""),
    sentiment: str = Form(default=""),
    error_type: str = Form(default="normal"),
    resident_type: str = Form(default=""),
    family_status: str = Form(default=""),
    follow_up_depth: int = Form(default=3, ge=1, le=10),
    data_path: str = Form(default="", max_length=1000, min_length=0),
    number_of_questions: int = Form(default=5, ge=1, le=10000),
    sample_size: int = Form(default=5, ge=1, le=100),
    products_group_size: int = Form(default=1, ge=1, le=1000),
    group_columns: str = Form(default=""),  # ex - brand,category,gender
    model_name: str = Form(default="gpt-3.5-turbo"),
    prompt_key: str = Form(default="prompt_key_readme"),
    llm_type: str = Form(default=".txt"),
    generator_type: str = Form(default="text"),
    metadata: str = Form(default=""),
    crawl_depth: int = Form(default=1, ge=1, le=10),
    files: List[UploadFile] = File(...),
):
    if get_user_info(userId) == False:
        return {"message": "Unauthorized"}
    data_paths = []
    if files and len(files) > 0:
        # with open(os.path.join(upload_directory, file[0].filename), "wb") as f:
        #     file_content = await file[0].read()
        #     f.write(file_content)
        # if data_path == "":  # non empty if html links are provided
        #     data_path = os.path.join(upload_directory, file[0].filename)
        for i, file in enumerate(files):
            with open(os.path.join(upload_directory, file.filename), "wb") as f:
                file_content = await file.read()
                f.write(file_content)
            data_paths.append(os.path.join(upload_directory, file.filename))
        if data_path != "":
            # Set data_path to the path of the first file if data_path is empty
            data_paths = data_path.split(",")

    else:
        if data_path == "":
            return {"message": "No file or html web link provided as data source"}
        data_paths = data_path.split(",")

    if llm_type == ".ner":
        generator_type = "ner"
        metadata = data_paths[0]
    logger.info(f"Data path: {data_path}")
    logger.info(f"Data paths: {data_paths}")
    logger.info(f"Number of questions: {number_of_questions}")
    logger.info(f"Sample size: {sample_size}")
    logger.info(f"Products group size: {products_group_size}")
    logger.info(f"Group columns: {group_columns}")
    logger.info(f"Model name: {model_name}")
    logger.info(f"Prompt key: {prompt_key}")
    logger.info(f"LLM type: {llm_type}")
    logger.info(f"Generator type: {generator_type}")
    logger.info(f"Metadata: {metadata}")
    logger.info(f"Crawl depth: {crawl_depth}")
    gen_id = uuid.uuid4().hex
    output_file = os.path.join(output_directory, f"{gen_id}.json")
    logger.info(f"Output file: {output_file}")
    grouped_columns = []
    if group_columns:
        for column in group_columns.split(","):
            grouped_columns.append(column)

    try:
        # Create a new Dataset instance
        dataset = Dataset(
            name=name,
            gen_id=gen_id,
            sample_size=sample_size,
            number_of_questions=number_of_questions,
            orgid=orgId,
            userid=userId,
            dataset_type=dataset_type,
            model_name=model_name,
            chunk_size=chunk_size,
            persona=persona,
            behavior=behavior,
            demographic=demographic,
            sentiment=sentiment,
            error_type=error_type,
            resident_type=resident_type,
            family_status=family_status,
        )

        # Add the instance to the session and flush to generate the ID
        db.session.add(dataset)
        db.session.flush()

        # Now you can access the ID
        dataset_id = dataset.id

        # Commit the changes to the database
        db.session.commit()

        background_tasks.add_task(
            qa_generator,
            data_paths,
            number_of_questions,
            sample_size,
            products_group_size,
            grouped_columns,
            output_file,
            model_name,
            prompt_key,
            llm_type,
            generator_type,
            metadata,
            crawl_depth,
            dataset_id,
            orgId,
            userId,
            persona=persona,
            behavior=behavior,
            demographic=demographic,
            sentiment=sentiment,
            error_type=error_type,
            follow_up_depth=follow_up_depth,
            resident_type=resident_type,
            family_status=family_status,
        )

        return JSONResponse(
            content={
                "message": "Generator in progress, Use the /download/ endpoint to check the status of the generator",
                "gen_id": gen_id,
                "dataset_id": dataset_id,
            }
        )
    except SQLAlchemyError as e:
        # Handle the exception (e.g., log the error, rollback the session)
        db.session.rollback()
        logger.info({"error": f"Error during dataset insertion: {e}"})
        return JSONResponse(
            content={
                "message": "Oops! Something went wrong. Please try again.",
                "gen_id": -1,
            }
        )


@app.get("/api/dataset/search", response_model=List[DatasetResponse])
async def get_dataset(
    search: str,
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = (
        select(Dataset)
        .filter(
            Dataset.orgid == org_id,
            Dataset.name.ilike(f"%{search}%"),  # Use ilike for case-insensitive search
        )
        .order_by(desc(Dataset.ts))
    )  # Add order_by clause to sort by ts in descending order

    results = db.execute(query).scalars().all()
    return results


@app.get("/api/dataset/list", response_model=List[DatasetResponse])
async def get_dataset(
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = (
        select(Dataset).filter(Dataset.orgid == org_id).order_by(desc(Dataset.ts))
    )
    results = db.execute(query).scalars().all()
    return results


@app.get("/api/dataset", response_model=DatasetResponse)
async def get_dataset(
    dataset_id: int,
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = select(Dataset).filter(Dataset.id == dataset_id, Dataset.orgid == org_id)
    results = db.execute(query).scalars().first()
    return results

@app.get("/api/dataset/{gen_id}")
async def download(gen_id: str):
    """
    Downloads a dataset with the given `gen_id` and returns a FileResponse object if the dataset exists.
    If the dataset does not exist, returns a dictionary with a "message" key and a corresponding error message.
    """
    output_file = os.path.join(output_directory, f"{gen_id}.json")
    logger.info(f"Output file: {output_file}")
    if os.path.exists(output_file):
        return FileResponse(
            output_file,
            headers={"Content-Disposition": f"attachment; filename={output_file}"},
        )
    else:
        return {"message": "Dataset not found"}


@app.get("/api/llm-endpoints/list", response_model=List[LLMEndpointResponse])
async def get_llm_endpoints(
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = select(LLMEndpoint).filter(LLMEndpoint.orgid == org_id).order_by(desc(LLMEndpoint.ts))
    results = db.execute(query).scalars().all()
    return results


@app.get("/api/llm-endpoint", response_model=LLMEndpointResponse)
async def get_endpoint(
    llm_endpoint_id: int,
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = select(LLMEndpoint).filter(
        LLMEndpoint.id == llm_endpoint_id, LLMEndpoint.orgid == org_id
    )
    results = db.execute(query).scalars().first()
    return results

@app.delete("/api/llm-endpoint")
async def delete_endpoint(
    llm_endpoint_id: int,
    org_id: str = Query("", max_length=1000, min_length=0),
    db: Session = Depends(get_db),
):
    query = select(LLMEndpoint).filter(
        LLMEndpoint.id == llm_endpoint_id, LLMEndpoint.orgid == org_id
    )
    results = db.execute(query).scalars().first()
    if results:
        db.delete(results)
        db.commit()
        return {"message": "Endpoint deleted successfully"}
    else:
        return {"message": "Endpoint not found"}


@app.post("/api/endpoint/add")
async def generator(
    name: str = Form(default=""),
    endpoint_url: str = Form(default=""),
    userId: str = Form(default="-1"),
    orgId: str = Form(default="-1"),
    access_token: str = Form(default=""),
    payload_format: str = Form(default=""),
    payload_user_key: str = Form(default=""),
    payload_message_key: str = Form(default=""),
    http_method: str = Form(default=""),
    requests_per_minute: int = Form(default=10, ge=1, le=100),
):
    if name == "":
        return {"message": "No name provided for endpoint"}
    if endpoint_url == "":
        return {"message": "No endpoint url provided for endpoint"}
    if get_user_info(userId) == False:
        return {"message": "Unauthorized"}
    try:
        # Create a new Dataset instance
        endpoint = LLMEndpoint(
            name=name,
            endpoint_url=endpoint_url,
            orgid=orgId,
            userid=userId,
            access_token=access_token,
            payload_format=payload_format,
            payload_user_key=payload_user_key,
            payload_message_key=payload_message_key,
            http_method=http_method,
            requests_per_minute=requests_per_minute,
        )

        # Add the instance to the session and flush to generate the ID
        db.session.add(endpoint)
        db.session.flush()

        # Now you can access the ID
        endpoint_id = endpoint.id

        # Commit the changes to the database
        db.session.commit()

        return JSONResponse(
            content={
                "message": "Endpoint added successfully",
                "endpoint_id": endpoint_id,
            }
        )
    except SQLAlchemyError as e:
        # Handle the exception (e.g., log the error, rollback the session)
        db.session.rollback()
        logger.info({"error": f"Error during endpoint insertion: {e}"})
        return JSONResponse(
            content={
                "message": "Oops! Something went wrong. Please try again.",
                "endpoint_id": -1,
            }
        )

@app.get("/api/qa-data", response_model=List[QADataResponse])
async def get_qa_data(
    dataset_id: int,
    org_id: str = Query("", max_length=1000, min_length=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1),
    db: Session = Depends(get_db),
):
    query = (
        select(QAData)
        .filter(QAData.dataset_id == dataset_id, QAData.orgid == org_id)
        .offset(skip)
        .limit(limit)
    )
    results = db.execute(query).scalars().all()
    return results

@app.post("/api/evaluate")
async def evaluate(
    gen_id: str = Form(...),
    llm_endpoint: str = Form(...),
    log_wandb: bool = Form(default=False),
    sampling_factor: float = Form(default=1.0, ge=0.0, le=1.0),
):
    if not gen_id:
        raise HTTPException(status_code=400, detail="gen_id is required")

    qa_file = os.path.join(output_directory, f"{gen_id}.json")
    logger.info(f"Output file: {qa_file}")

    if os.path.exists(qa_file) and os.path.getsize(qa_file) > 0:
        endpoint_configs = [{"name": f"llm-f{gen_id}", "url": llm_endpoint}]
        output_file = os.path.join(output_directory, f"ranked_{gen_id}.json")
        await evaluate_qa_data(
            qa_file, endpoint_configs, log_wandb, output_file, sampling_factor
        )
        return JSONResponse(
            content={
                "message": "Ranker is complete, Use the /ranking/id endpoint to download evaluated ranked reports for each question",
                "gen_id": gen_id,
            }
        )
    else:
        return {"message": "Dataset with id {gen_id} not found"}


@app.get("/api/ranking/{gen_id}")
async def ranked_reports(gen_id: str):
    """
    Downloads a dataset with the given `gen_id` and returns a FileResponse object if the dataset exists.
    If the dataset does not exist, returns a dictionary with a "message" key and a corresponding error message.
    """
    output_file = os.path.join(output_directory, f"ranked_{gen_id}.json")
    if os.path.exists(output_file):
        return FileResponse(
            output_file,
            headers={"Content-Disposition": f"attachment; filename={output_file}"},
        )
    else:
        return {"message": "Ranked dataset not found"}


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    for i in range(10):
        await websocket.send_text(f"Streamed message {i}")
        await asyncio.sleep(1)  # Simulate a delay between messages

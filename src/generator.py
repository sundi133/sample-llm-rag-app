import logging
import argparse
import asyncio

from langchain.chat_models import ChatOpenAI
from typing import List
from .utils import select_processor, select_llm

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)


logger = logging.getLogger(__name__)


def qa_generator(
    data_path: List[str],
    number_of_questions: int,
    sample_size: int,
    products_group_size: int,
    group_columns: List[str],
    output_file: str,
    model_name: str,
    prompt_key: str,
    llm_type: str,
    generator_type: str,
    metadata_path: str,
    crawl_depth: int,
    gen_id: str,
    orgId: str,
    userId: str,
    persona: str,
    behavior: str,
    demographic: str,
    sentiment: str,
    error_type: str,
    follow_up_depth: int,
    resident_type: str,
    family_status: str,
) -> None:
    """
    Generate questions and answers or training dataset from provided files

    Args:
        data_path (str): Path to the input data file
        number_of_questions (int): Number of questions to generate
        sample_size (int): Number of samples to use for generating questions
        products_group_size (int): Number of products to group together
        group_columns (List[str]): List of columns to group by
        output_file (str): Path to the output file
        model_name (str): Name of the OpenAI model to use
        prompt_key (str): Key to use for the prompt
        llm_type (str): Type of LLM to use
        generator_type (str): Type of generator to use
        metadata_path (str): Path to the metadata file
        crawl_depth (int): Depth to crawl for HTML files
    """
    llm_openai_gpt = ChatOpenAI(
        temperature=0.75,
        model=model_name,
        request_timeout=120,
    )

    logger.info("Starting Question Generator")

    qa_generator = select_llm(generator_type, llm_openai_gpt, prompt_key, verbose=True)

    data_processor = select_processor(data_path, llm_type, dataset_id=gen_id)

    if llm_type == ".ner":
        data_processor.set_entity(metadata_path)
    if llm_type == ".html":
        data_processor.set_depth(crawl_depth)

    data_processor.setTenant(orgId)
    data_processor.setUser(userId)
    sim_profile = {"persona": persona, "behavior": behavior, "demographic": demographic, "sentiment": sentiment, "error_type": error_type, "follow_up_depth": follow_up_depth, "resident_type": resident_type, "family_status": family_status}
    data_processor.setSimProfile(sim_profile)
    df = data_processor.parse()

    randomized_samples = data_processor.randomize_samples(
        df, sample_size, products_group_size, group_columns
    )

    data_processor.generate_qa_pairs(
        randomized_samples,
        df,
        sample_size,
        products_group_size,
        group_columns,
        number_of_questions,
        qa_generator,
    )

    data_processor.write(output_file)

    # Log completion of Question Generator
    logger.info("Completed Question Generator")

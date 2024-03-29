import nltk
import argparse
import asyncio
import logging
import json
import os
import random

from rouge_score import rouge_scorer

from typing import List
from .utils import read_endpoint_configurations, read_qa_data, get_llm_answer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)


async def evaluate_qa_data(
    qa_data_path: str,
    endpoint_configs: dict(),
    output_file: str = "ranking.json",
    sampling_factor: float = 1.0,
) -> None:
    """
    Evaluate the quality of answers generated by different endpoints for a given set of questions.

    Args:
        qa_data_path (str): The path to the JSON file containing the QA data.
        qa_endpoints (str): The path to the JSON file containing the endpoint configurations.

    Returns:
        None
    """

    # Read the qa dataset
    qa_data = read_qa_data(qa_data_path)

    for endpoint_config in endpoint_configs:
        logger.info(f"Endpoint Name: {endpoint_config['name']}")
        logger.info(f"Endpoint URL: {endpoint_config['url']}")
    for entry in qa_data:
        question = entry.get("question", "")
        answer = entry.get("answer", "")
        logger.info(f"Question: {question}")
        logger.info(f"Answer: {answer}")

    # sample from qa_data randomly based on a sampling factor
    sample_size = (int)(sampling_factor * len(qa_data))
    qa_data = random.sample(qa_data, sample_size)

    question_ranking = []
    scorer = rouge_scorer.RougeScorer(["rouge1", "rougeL"], use_stemmer=True)

    for entry in qa_data:
        question = entry.get("question", "")
        reference_answer = entry.get("answer", "")
        logger.info(f"Question: {question}")
        logger.info(f"Answer: {reference_answer}")
        for endpoint_config in endpoint_configs:
            candidate = await get_llm_answer(question, endpoint_config)

            # Calculate ROUGE-L score
            rouge_l_score_val = scorer.score(reference_answer, candidate)[
                "rougeL"
            ].fmeasure

            logger.info(
                {
                    "endpoint_name": endpoint_config["name"],
                    "url": endpoint_config["url"],
                    "question": question,
                    "expected_response": reference_answer,
                    "endpoint_response": candidate,
                    "rouge_l_score": rouge_l_score_val,
                }
            )
            question_ranking.append(
                {
                    "endpoint_name": endpoint_config["name"],
                    "url": endpoint_config["url"],
                    "question": question,
                    "expected_response": reference_answer,
                    "endpoint_response": candidate,
                    "rouge_l_score": rouge_l_score_val,
                }
            )

        # Sort the endpoints based on their scores (highest to lowest)
        question_ranking = sorted(
            question_ranking,
            key=lambda x: (-x["rouge_l_score"], -x["bleu_score"], -x["meteor_score"]),
        )

    question_ranking = sorted(
        question_ranking,
        key=lambda x: (-x["rouge_l_score"], -x["bleu_score"], -x["meteor_score"]),
    )

    for ranking in question_ranking:
        logger.info(f"Question: {ranking['question']}")
        logger.info(
            {
                "question": ranking["question"],
                "expected_response": ranking["expected_response"],
                "endpoint_response": ranking["endpoint_response"],
                "endpoint_name": ranking["endpoint_name"],
                "url": ranking["url"],
                "rouge_l_score": ranking["rouge_l_score"],
            }
        )

    # rm file if exists
    if os.path.exists(output_file):
        os.remove(output_file)

    with open(output_file, "w") as op:
        json.dump(question_ranking, op, indent=4)

    if os.path.exists(output_file):
        logger.info(f"Ranking is completed and saved to {output_file}")

    logger.info("Ranking is completed")

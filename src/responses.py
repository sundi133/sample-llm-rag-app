import os
import uuid
import asyncio
import logging
import json
import os, sys
import requests

from typing import List, Optional
from pydantic import BaseModel, ValidationError

from .models import Base, Dataset, QAData, LLMEndpoint
from datetime import datetime


class DatasetResponse(BaseModel):
    id: int
    name: str
    gen_id: str
    sample_size: int
    number_of_questions: int
    orgid: str
    userid: str
    ts: datetime
    dataset_type: Optional[str]  # Allow dataset_type to be None
    model_name: Optional[str]  # Allow model_name to be None
    chunk_size: Optional[int]  # Allow chunk_size to be None
    persona: Optional[str]  # Allow persona to be None
    behavior: Optional[str]  # Allow behavior to be None
    demographic: Optional[str]  # Allow demographic to be None
    sentiment: Optional[str]  # Allow sentiment to be None
    error_type: Optional[str]  # Allow error_type to be None
    resident_type: Optional[str]  # Allow resident_type to be None
    family_status: Optional[str]  # Allow family_status to be None

class QADataResponse(BaseModel):
    id: int
    userid: str
    orgid: str
    dataset_id: int
    ts: datetime
    chat_messages: str
    reference_chunk: str

class LLMEndpointResponse(BaseModel):
    id: int
    userid: str
    orgid: str
    name: str
    endpoint_url: str
    ts: datetime
    access_token: Optional[str]  # Allow access_token to be None
    payload_format: Optional[str]  # Allow payload_format to be None
    payload_user_key: Optional[str]  # Allow payload_user_key to be None
    payload_message_key: Optional[str]  # Allow payload_message_key to be None
    http_method: Optional[str]  # Allow http_method to be None
    requests_per_minute: Optional[int]  # Allow requests_per_minute to be None

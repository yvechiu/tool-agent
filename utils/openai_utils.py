import os
from langchain_openai import ChatOpenAI

from dotenv import load_dotenv
load_dotenv(".env")

llm = ChatOpenAI(
    model="gpt-4o",
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0,
    verbose=False,
)
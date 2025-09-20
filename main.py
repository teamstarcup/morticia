import logging
import os
import sys

import dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

dotenv.load_dotenv(".env")

import src.awaitable.modal
from src.bot import MorticiaBot
from src.morticia import Morticia

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.DEBUG,
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

token = os.environ.get("GITHUB_TOKEN")
username = os.environ.get("GITHUB_BOT_USERNAME")
email = os.environ.get("GITHUB_BOT_EMAIL")

db_host = os.environ.get("POSTGRES_HOST")
db_port = os.environ.get("POSTGRES_PORT")
db_user = os.environ.get("POSTGRES_USER")
db_pass = os.environ.get("POSTGRES_PASSWORD")
db_name = os.environ.get("POSTGRES_DB")
engine = create_engine(f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}')


with Session(engine) as session:
    morticia = Morticia(token, session)
    bot = src.bot.create_bot(morticia)
    bot.session = session
    bot.run(os.environ.get("DISCORD_TOKEN"))

morticia.close()

import logging

from flask import Flask
from flask_cors import CORS

from app.utils.logger import setup_logging

# Configure structured logging for the backend
setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    # Must run before app.routes is imported, because the services read
    # PROTEIN_LLM_* / NCBI_* at construction time.
    from app import config  # noqa: F401  (side-effecting import)

    app = Flask(__name__)
    CORS(app)

    from app.routes import bp

    app.register_blueprint(bp)
    return app


app = create_app()
logger.info("Flask application created and routes registered")

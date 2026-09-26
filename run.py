import logging

from app import app

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting Protein Friend Finder server on port 5000")
    logger.info("=" * 60)
    app.run(debug=True, port=5000)

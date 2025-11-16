import logging
import sys
import os
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
os.makedirs(logs_dir, exist_ok=True)

# Configure logger
logger = logging.getLogger("easyvote")
logger.setLevel(logging.INFO)

# Create handlers
console_handler = logging.StreamHandler(sys.stdout)
file_handler = RotatingFileHandler(
    os.path.join(logs_dir, "app.log"), 
    maxBytes=10485760,  # 10MB
    backupCount=5
)

# Create formatters
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s"
)

# Set formatters
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)
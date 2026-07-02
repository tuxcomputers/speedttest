import logging
import time


def get_logger(name):
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)sZ %(levelname)s [%(name)s] %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )
    return logging.getLogger(name)

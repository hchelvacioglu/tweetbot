import sys
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import twitter_manager

def run_test():
    test_message = "⚽ Merhaba X! Flaş Futbol haber botu aktif edildi. Spor haberleri artık burada! 🚀"
    
    logger.info("Sending test tweet via GetXAPI...")
    success = twitter_manager.post_tweet(test_message)
    
    if success:
        logger.info("✅ TEST SUCCESSFUL! Tweet posted to X.")
        sys.exit(0)
    else:
        logger.error("❌ TEST FAILED! Could not post tweet.")
        sys.exit(1)

if __name__ == "__main__":
    run_test()

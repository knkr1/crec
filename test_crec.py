import unittest
from crec.sources.youtube import YouTubeHandler
from crec.sources.twitter import TwitterHandler

class TestYouTubeHandler(unittest.TestCase):
    def setUp(self):
        self.handler = YouTubeHandler()

    def test_youtube_url_validation(self):
        valid_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        ]
        invalid_urls = [
            "https://www.youtube.com",
            "https://example.com",
            "not a url"
        ]

        for url in valid_urls:
            self.assertTrue(self.handler.can_handle(url), f"Should handle {url}")

        for url in invalid_urls:
            self.assertFalse(self.handler.can_handle(url), f"Should not handle {url}")

class TestTwitterHandler(unittest.TestCase):
    def setUp(self):
        self.handler = TwitterHandler()

    def test_twitter_url_validation(self):
        valid_urls = [
            "https://twitter.com/username/status/123456789",
            "https://x.com/username/status/123456789"
        ]
        invalid_urls = [
            "https://twitter.com",
            "https://example.com",
            "not a url"
        ]

        for url in valid_urls:
            self.assertTrue(self.handler.can_handle(url), f"Should handle {url}")

        for url in invalid_urls:
            self.assertFalse(self.handler.can_handle(url), f"Should not handle {url}")

if __name__ == '__main__':
    unittest.main() 
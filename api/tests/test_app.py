import os
import unittest
from unittest.mock import patch # Import patch
from bs4 import BeautifulSoup
# from api.main import app # Assuming your Flask app instance is named 'app' in main.py

class TestApp(unittest.TestCase):

    def setUp(self):
        self.original_report_form_url = os.environ.get('REPORT_FORM_URL')
        self.original_supabase_url = os.environ.get('SUPABASE_URL') # Store original
        self.original_supabase_key = os.environ.get('SUPABASE_KEY') # Store original

        # Set dummy env vars FIRST so api.main can be imported by patch
        os.environ['SUPABASE_URL'] = 'http://dummy.supabase.co'
        os.environ['SUPABASE_KEY'] = 'dummykeydummykeydummykeydummykeydumm' # Ensure it's a plausible key format

        if 'REPORT_FORM_URL' in os.environ:
            del os.environ['REPORT_FORM_URL']

        # Patch 'supabase.create_client' - where it's originally defined and looked up
        # The target is 'api.main.create_client' because create_client is imported into main.py's namespace
        self.mock_supabase_create_client = patch('supabase.create_client').start()
        # from unittest.mock import MagicMock
        # self.mock_supabase_create_client.return_value = MagicMock() # Configure mock if needed

        from api.main import app
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        patch.stopall() # Stops all active patches

        if self.original_report_form_url is not None:
            os.environ['REPORT_FORM_URL'] = self.original_report_form_url
        elif 'REPORT_FORM_URL' in os.environ:
            del os.environ['REPORT_FORM_URL']

        if self.original_supabase_url is not None: # Restore
            os.environ['SUPABASE_URL'] = self.original_supabase_url
        elif 'SUPABASE_URL' in os.environ:
            del os.environ['SUPABASE_URL']

        if self.original_supabase_key is not None: # Restore
            os.environ['SUPABASE_KEY'] = self.original_supabase_key
        elif 'SUPABASE_KEY' in os.environ:
            del os.environ['SUPABASE_KEY']

    def test_home_page_default_report_link(self):
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.data, 'html.parser')
        report_button = soup.find('a', class_='report-btn')
        self.assertIsNotNone(report_button)
        self.assertEqual(report_button['href'], 'https://forms.gle/vbALkyz6zaZStF4k6')

    def test_home_page_custom_report_link(self):
        custom_url = "https://custom.example.com/report"
        os.environ['REPORT_FORM_URL'] = custom_url
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.data, 'html.parser')
        report_button = soup.find('a', class_='report-btn')
        self.assertIsNotNone(report_button)
        self.assertEqual(report_button['href'], custom_url)

if __name__ == '__main__':
    unittest.main()

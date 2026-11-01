from urllib.parse import urljoin

import requests

from ..config.settings import Settings


class CanvasAPI:
    settings = Settings()
    base_url = settings.base_url
    headers = {'Authorization': f"Bearer {settings.token}"}

    def get(self, endpoint: str, params: dict = None):
        """return response json"""
        response = requests.get(
            url=urljoin(self.base_url, endpoint),
            headers=self.headers,
            params=params
        )
        response.raise_for_status()
        return response.json()

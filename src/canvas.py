import json
import os
from urllib.parse import urljoin

import requests


class Canvas:
    def __init__(self,
                 api_url: str,
                 access_token: str,
                 save_dir: str = "downloads/",
                 cache_dir: str = ".cache/",
                 ) -> None:
        """
        Initialize Canvas object.
        :param api_url:
        :param access_token:
        """
        self.api_url = api_url
        self.access_token = access_token

        self.headers = {'Authorization': f"Bearer {access_token}"}

        self.save_dir = save_dir
        self.cache_dir = cache_dir

    def get_course(self):
        pass

    def update_enrollments(self):
        json_data = self._fetch("users/self/enrollments")

        os.makedirs(self.cache_dir, exist_ok=True)

        target_file = self._enrollments_file

        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        print(f"Enrollments Updated.")

    @property
    def enrollments(self):
        target_file = self._enrollments_file

        if not os.path.exists(target_file):
            self.update_enrollments()

        with open(target_file, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        return json_data

    @property
    def _enrollments_file(self) -> str:
        return self._cache_file("enrollments.json")

    def _cache_file(self, filename: str) -> str:
        return os.path.join(self.cache_dir, filename)

    def _fetch(self, route: str) -> dict:
        """
        Fetch data from a specific route.
        :param route:
        :return:
        """
        url = urljoin(self.api_url, route)
        response = requests.get(url, headers=self.headers)
        return response.json()

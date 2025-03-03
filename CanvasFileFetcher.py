import requests, time, os, csv


class CanvasFileFetcher:
    def __init__(self, domain: str, token: str, save_dir: str = '.'):
        self.base_url = f"https://{domain}/api/v1"
        self.headers = {'Authorization': f"Bearer {token}"}
        self.save_dir = save_dir
        self.file_records = []

    def request_data(self, endpoint: str, max_retries=10):
        url = f"{self.base_url}{endpoint}"
        for attempt in range(max_retries):
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Attempt {attempt + 1} failed: {response.status_code}")
                time.sleep(2)
        print("Max retries reached.")
        return []

    def get_enrolled_courses(self):
        return self.request_data("/users/self/enrollments")

    def get_course_name(self, course_id: int | str):
        course_info = self.request_data(f"/courses/{course_id}")
        return course_info.get('name', f'Course_{course_id}')

    def get_course_files(self, course_id: int | str):
        return self.request_data(f"/courses/{course_id}/files")

    def get_folder_info(self, folder_id: int | str):
        return self.request_data(f"/folders/{folder_id}")

    def build_folder_path(self, folder_id: int | str):
        folder_info = self.get_folder_info(folder_id)
        # print(folder_id, folder_info)
        full_path = folder_info['full_name']
        cleaned_path = os.path.relpath(full_path, "course files")
        if cleaned_path == ".": cleaned_path = ""

        return cleaned_path

        # --- recursive approach ---
        # path_parts = []
        #
        # while folder_id:
        #     folder_info = self.get_folder_info(folder_id)
        #     print(folder_id, folder_info)
        #     folder_name = folder_info['name']
        #     path_parts.insert(0, folder_name)
        #     folder_id = folder_info.get('parent_folder_id')
        #
        # # remove prefix "course files"
        # if path_parts and path_parts[0].lower() == "course files":
        #     path_parts.pop(0)
        #
        # return '/'.join(path_parts) if path_parts else ''
        # --------------------------

    def collect_files(self):
        courses = self.get_enrolled_courses()
        for course in courses:
            course_id = course['course_id']
            course_name = self.get_course_name(course_id)

            files = self.get_course_files(course_id)
            for file in files:
                folder_path = self.build_folder_path(file['folder_id'])

                # Save info
                if file['url'] != "":
                    self.file_records.append({
                    'Course Name': course_name,
                    'Folder Path': folder_path,
                    'File Name': file['display_name'],
                    'File URL': file['url']
                })

    def export_to_csv(self, filename: str = 'canvas_files.csv'):
        if not self.file_records:
            print("No file data to export.")
            return

        keys = self.file_records[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.file_records)

        print(f"File data exported to {filename}")

    def download_files_from_csv(self, csv_file: str):
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                save_path = os.path.join(self.save_dir, row['Course Name'], row['Folder Path'])
                download_file(row['File URL'], row['File Name'], save_path)


def download_file(file_url: str, filename: str, save_dir: str):
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, filename)

    response = requests.get(file_url)
    with open(file_path, 'wb') as f:
        f.write(response.content)
    print(f"Downloaded: {file_path}")

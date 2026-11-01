from pathlib import Path

from pydantic import Field
from pydantic_configuration import AbstractConfig

from src.utils.path import root_path


class UserSettings(AbstractConfig):
    def _env_file(self) -> Path:
        return root_path / '.env'

    def _yaml_file(self) -> Path:
        return root_path / 'config' / 'user_config.yml'

    website: str = Field(default_factory=str, alias="WEBSITE")
    """Domain of your institute"""

    access_token: str = Field(default_factory=str, alias="ACCESS_TOKEN")
    """API access token"""

    save_dir: str = Field(default_factory=str, alias="SAVE_DIR")
    """Directory to save data"""


settings = UserSettings()

if __name__ == "__main__":
    print(settings.model_dump())

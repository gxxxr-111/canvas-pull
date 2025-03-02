<h1 align="center">Canvas Pull</h1>

<p align="center">
    <img src="https://img.shields.io/badge/license-MIT-green)" />
    <img src="https://img.shields.io/badge/version-1.0.0-blue" />
</p>


Canvas Pull is a Python project designed to automate content retrieval tasks from Canvas.

# General Installation

1. Generate your API access token

    [How to generate?](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-in-my-user-account/ta-p/615312)

2. Edit `config.yml`

    ```yaml
    WEBSITE: oc.sjtu.edu.cn # Domain of your institute (Should be based on Canvas LMS).
    ACCESS_TOKEN: xxxxxx    # Your API access token. You can get it from your Canvas LMS.
    SAVE_DIR: .             # Directory to save the data. Default is the current directory.
    ```

3. Run `main.py`

    ```bash
    python path/to/main.py
    ```
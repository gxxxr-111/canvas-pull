<h1 align="center">Canvas Pull</h1>

<p align="center">
    <img src="https://img.shields.io/badge/license-MIT-green)" />
    <img src="https://img.shields.io/badge/version-1.0.0-blue" />
</p>


Canvas Pull is a Python project designed to automate content retrieval tasks from Canvas.

# General Installation

1. Generate your API access token

    [How to generate?](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-in-my-user-account/ta-p/615312)

    <div style="border-left: 3px solid #f39c12; padding-left: 10px; margin-bottom: 2px; font-family: sans-serif; font-size: 14px; ">
    <div style="padding-bottom: 5px">    
        <strong style="color: #856404; ">⚠️ Warning</strong>
    </div>
    <ol>
        <li><strong>NEVER</strong> share your API access token, otherwise it could be used maliciously.</li>
        <li>Generate <strong>short-term</strong> API access tokens and update them frequently to ensure safety.</li>
    </ol>
    </div>


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



# Run in Bash & Manage with Git

1. Give execution permission to `canvas-pull`

    ```bash
    chmod +x canvas-pull
    ```

2.  Add this directory to `PATH` variable.
    
    Edit `~/.bashrc`

    ```bash
    nano ~/.bashrc
    # or vim ~/.bashrc
    ```

    Add the following line and save

    ```bash
    export PATH="$PATH:/path/to/canvas-pull"
    ```

    Reload `.bashrc` settings

    ```bash
    source ~/.bashrc
    # or . ~/.bashrc
    ``` 


    <div style="border-left: 3px solid rgb(34, 80, 207); padding-left: 10px; margin-bottom: 2px; font-family: sans-serif; font-size: 14px; ">
    <div style="padding-bottom: 5px">    
        <strong style="color: rgb(34, 80, 207);">💡 Note</strong>
    </div>
    
    For macOS users using `zsh`, just replace `.bashrc` with `.zshrc`.

    </div>


3. Run in bash

    ```bash
    canvas-pull
    ```

4. Check file changes

    You can utilize `VSCode` to check the commit tree and view the differences. For macOS users, some third-party applications such as `Fork` are also excellent options. 

# Imperfections

Since the generation rule of `file_id` is currently unclear. There is temporarily no way to compare remote files with local files without downloading them from the remote server. If there is any approach to distinguish files at both ends using unique id, hash functions or others, you are welcome to open an issue!
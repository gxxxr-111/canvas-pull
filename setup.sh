#!/bin/bash

mkdir -p canvas

# 在 canvas 文件夹中创建 pull 脚本
cat << 'EOF' > canvas/pull
#!/bin/bash

# 确保脚本在项目的根目录运行
cd "$(dirname "$0")/.."

# 检查是否安装了 Python 3
if ! command -v python3 &>/dev/null; then
    echo "Python 3 is not installed. Please install it first."
    exit 1
fi

# 检查是否存在 main.py 文件
if [ ! -f "main.py" ]; then
    echo "main.py not found in the current directory."
    exit 1
fi

# 确保脚本具有执行权限
if [ ! -x "$(command -v chmod)" ]; then
    echo "chmod command not found. Please install it first."
    exit 1
fi

# 执行 main.py
python3 main.py
EOF

# 给 pull 脚本赋予可执行权限
chmod +x canvas/pull

# 提示用户脚本已创建并赋予权限
echo "The 'pull' script has been created and made executable."

# 检查并提示用户是否需要将 canvas 目录添加到 PATH 中
echo "If you want to run the script from anywhere, add the following line to your ~/.bashrc or ~/.bash_profile:"
echo 'export PATH="$PATH:/path/to/your/project/canvas"'
echo "Then run: source ~/.bashrc or source ~/.bash_profile"

# 完成脚本
echo "Setup complete!"

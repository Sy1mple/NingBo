"""终端隐藏输入讯飞密钥，写入本机配置，不打印密钥。"""
import getpass
import os
from pathlib import Path
import tempfile


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / '.env.voice'
    entries = []
    for name in ('XFYUN_APP_ID', 'XFYUN_API_KEY', 'XFYUN_API_SECRET'):
        value = getpass.getpass(name + '（输入隐藏）: ').strip()
        if not value or any(c.isspace() for c in value) or any(c in value for c in "'\"="):
            raise ValueError(name + ' 不能为空或包含空格、引号、等号')
        entries.append(name + '=' + value)
    descriptor, temporary = tempfile.mkstemp(prefix='.voice-config-', dir=str(root))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write('\n'.join(entries) + '\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('讯飞密钥已保存到 ' + str(target) + '，没有打印密钥。')
    print('在屏幕上点击停止聆听，再点击开始聆听，即可读取新配置。')


if __name__ == '__main__':
    main()

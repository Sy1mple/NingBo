"""隐藏输入讯飞人脸专用密钥；免费次数/期限以控制台实际授权为准。"""
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import tempfile


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / '.env.face'
    print('请在同一讯飞应用开通自研「人脸比对」和「人脸检测和属性分析」，仅领取免费试用包。')
    print('请使用本项目专用应用；限额填控制台实际剩余免费次数，不购买或启用付费套餐。')
    entries = []
    for name in ('XFYUN_FACE_APP_ID', 'XFYUN_FACE_API_KEY', 'XFYUN_FACE_API_SECRET'):
        value = getpass.getpass(name + '（输入隐藏）: ').strip()
        if not value or any(c.isspace() for c in value) or any(c in value for c in "'\"="):
            raise ValueError(name + ' 不能为空或包含空格、引号、等号')
        entries.append(name + '=' + value)
    for name, label in (('XFYUN_FACE_DETECT_LIMIT', '人脸检测'), ('XFYUN_FACE_COMPARE_LIMIT', '人脸比对')):
        value = input(label + '剩余免费次数（正整数）: ').strip()
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError('免费次数须为正整数')
        entries.append(name + '=' + value)
    value = input('两项免费包较早的到期时间（带时区；两项控制台均显示“—”时填 -）: ').strip()
    if value != '-':
        expiry = datetime.fromisoformat(value)
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError('免费到期时间须带时区且晚于当前时间')
    entries.append('XFYUN_FACE_FREE_EXPIRES=' + value)
    descriptor, temporary = tempfile.mkstemp(prefix='.face-config-', dir=str(root))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write('\n'.join(entries) + '\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('人脸密钥和免费上限已保存到 ' + str(target) + '，未打印密钥。')
    print('停止并重新启动人脸识别以读取配置。已有用量不会重置，请保留 data/face_usage.sqlite3。')


if __name__ == '__main__':
    main()

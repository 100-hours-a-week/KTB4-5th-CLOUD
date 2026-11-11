"""검증 계정을 순차 생성하고 결과를 로컬에 보관한다. 기본은 개발 서버 200개.

prod 예: BASE_URL=https://dameokja.com COUNT=50 ACCOUNTS_FILE=prod-accounts.json python3 provision_accounts.py
"""
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('BASE_URL', 'https://dev.dameokja.com')
COUNT = int(os.environ.get('COUNT', 200))
TARGET = Path(__file__).parent / 'private' / os.environ.get('ACCOUNTS_FILE', 'accounts.json')


def save(data):
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = TARGET.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
    os.replace(temporary, TARGET)


def request(opener, jar, path, payload=None):
    headers = {'Origin': BASE, 'User-Agent': 'Dameokja-Account-Provisioner/1.0'}
    body = None
    if payload is not None:
        token = next((c.value for c in jar if c.name == 'XSRF-TOKEN'), None)
        if not token:
            raise RuntimeError('CSRF 쿠키가 없습니다.')
        headers.update({'Content-Type': 'application/json', 'X-XSRF-TOKEN': urllib.parse.unquote(token)})
        body = json.dumps(payload).encode()
    try:
        with opener.open(urllib.request.Request(BASE + path, data=body, headers=headers), timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        try:
            result = json.loads(error.read())
        except (ValueError, UnicodeError):
            result = {}
        raise RuntimeError(f'HTTP {error.code}, code={result.get("code", "unknown")}') from None


def main():
    if TARGET.exists():
        data = json.loads(TARGET.read_text(encoding='utf-8'))
    else:
        # 아이디는 영숫자 10자 이내이며 금칙어 test를 포함하지 않는다.
        prefix = 'vu' + secrets.token_hex(2)
        accounts = []
        used = set()
        for index in range(1, COUNT + 1):
            while True:
                password = 'K7' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(22))
                if password not in used and 'test' not in password.lower():
                    used.add(password)
                    break
            accounts.append({'loginId': f'{prefix}{index:03d}', 'password': password, 'status': 'pending'})
        data = {'baseUrl': BASE, 'accounts': accounts}
        save(data)
    if data['baseUrl'] != BASE or len(data['accounts']) != COUNT:
        raise RuntimeError('기존 계정 파일의 대상 또는 개수를 확인하세요.')
    for index, account in enumerate(data['accounts'], 1):
        if account['status'] == 'verified':
            continue
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request(opener, jar, '/api/v1/auth/csrf')
        credentials = {key: account[key] for key in ('loginId', 'password')}
        if account['status'] == 'pending':
            # 타임아웃 후 재실행으로 중복 생성하지 않도록 요청 전에 상태를 저장한다.
            account['status'] = 'attempting'
            save(data)
            code, response = request(opener, jar, '/api/v1/users', {**credentials, 'nickname': account['loginId']})
            if code != 201:
                raise RuntimeError(f'가입 응답 오류: {code}')
            account['status'] = 'created'
            account['refrigeratorIds'] = response.get('data', {}).get('activeRefrigeratorIds', [])
            save(data)
        # 가입 응답뿐 아니라 새 쿠키 저장소로 실제 로그인을 검증한다.
        jar.clear()
        request(opener, jar, '/api/v1/auth/csrf')
        code, response = request(opener, jar, '/api/v1/auth/sessions', credentials)
        if code != 200 or not any(c.name == 'accessToken' for c in jar):
            raise RuntimeError('로그인 검증 실패')
        account['refrigeratorIds'] = response.get('data', {}).get('activeRefrigeratorIds', [])
        if not account['refrigeratorIds']:
            raise RuntimeError('개인 냉장고가 없습니다.')
        account['status'] = 'verified'
        save(data)
        if index == 1 or index % 20 == 0:
            print(f'가입 및 로그인 검증: {index}/{COUNT}', flush=True)
        time.sleep(0.5)
    print(f'완료: 서로 다른 계정 {COUNT}개 가입·로그인 검증', flush=True)


if __name__ == '__main__':
    main()

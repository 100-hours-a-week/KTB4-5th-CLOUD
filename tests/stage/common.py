"""stage 관측 도구의 공통 읽기 전용 명령과 시각 처리입니다."""
import datetime as dt
import json
import subprocess
import urllib.parse
import urllib.request

ENDPOINT = '070D7A26E983A20EBF48361944AA2372.gr7.ap-northeast-2.eks.amazonaws.com'
ACCOUNT = '906462447513'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def command_json(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)
    if result.returncode:
        raise RuntimeError(f'{command[0]} 조회 실패: {result.stderr[-1000:]}')
    return json.loads(result.stdout)


def api(url, path, params=None):
    target = url.rstrip('/') + path
    if params:
        target += '?' + urllib.parse.urlencode(params)
    # 관측 API는 SSM 터널 또는 지정한 사설 주소를 통해 읽기만 합니다.
    with urllib.request.urlopen(target, timeout=15) as response:
        result = json.load(response)
    if result.get('status') != 'success':
        raise RuntimeError('관측 API가 success를 반환하지 않았습니다.')
    return result['data']


def query(url, expression):
    return api(url, '/api/v1/query', {'query': expression})['result']


def kube_args(parser, required=True):
    parser.add_argument('--kubeconfig', required=required)
    parser.add_argument('--server')
    parser.add_argument('--profile', default='dameokja-aws-cli')


class Kube:
    def __init__(self, args):
        self.prefix = ['kubectl', '--kubeconfig', str(args.kubeconfig), '--request-timeout=15s']
        if args.server:
            if args.server != 'https://localhost:18443':
                raise ValueError('EKS 터널 서버는 https://localhost:18443만 허용합니다.')
            self.prefix += ['--server', args.server, '--tls-server-name', ENDPOINT]

    def validate(self):
        config = command_json(['kubectl', '--kubeconfig', self.prefix[2], 'config', 'view', '--minify', '-o', 'json'])
        cluster = config['clusters'][0]
        if not cluster['name'].endswith(':cluster/dameokja-v3') or cluster['cluster']['server'] != f'https://{ENDPOINT}':
            raise ValueError('dameokja-v3 원본 kubeconfig가 아닙니다.')

    def get(self, *args):
        return command_json(self.prefix + ['get', *args, '-o', 'json'])


def aws(args, *command):
    return command_json(['aws', '--profile', args.profile, '--region', 'ap-northeast-2', *command, '--output', 'json'])

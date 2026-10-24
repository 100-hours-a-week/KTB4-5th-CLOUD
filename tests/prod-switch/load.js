import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// 실행 예: 승인 후 BASE_URL·ACCOUNT_FILE·RUN_ID·PROD_CONFIRMED를 지정하고 run.sh run으로 시작한다.
// 계정·냉장고는 실제 회원과 분리한다. 응답 본문·쿠키·비밀번호는 결과에 출력하지 않는다.
const base = __ENV.BASE_URL;
const runId = __ENV.RUN_ID || '';
const phase = __ENV.PHASE || '';
const mode = __ENV.MODE || 'dns';
const seconds = Number(__ENV.DURATION_SECONDS || 300);
const reads = Number(__ENV.READ_RPS || 5);
const writes = Number(__ENV.WRITE_RPS || 1);
if (base !== 'https://dameokja.com' || __ENV.PROD_CONFIRMED !== 'true') {
  throw new Error('운영 대상과 승인 확인이 필요합니다.');
}
if (!/^[a-zA-Z0-9_-]{6,24}$/.test(runId) || !/^[a-zA-Z0-9_-]{1,32}$/.test(phase)
    || !['dns', 'migration'].includes(mode)) throw new Error('실행 ID·단계·모드를 확인하세요.');
// 액세스 토큰 15분 만료를 부하 장애로 오인하지 않도록 한 실행은 10분 이내로 제한한다.
if (!Number.isInteger(seconds) || seconds < 30 || seconds > 600
    || !Number.isInteger(reads) || reads < 1 || reads > 10 || writes !== 1) {
  throw new Error('측정 30~600초, 읽기 1~10 RPS, 쓰기 1 RPS만 지원합니다.');
}
if (!__ENV.ACCOUNT_FILE || !__ENV.RESULT_FILE) throw new Error('계정·결과 파일 경로가 필요합니다.');
const account = JSON.parse(open(__ENV.ACCOUNT_FILE));
if (account.baseUrl !== base || account.testOnly !== true || !account.loginId || !account.password
    || !/^\d+$/.test(String(account.refrigeratorId)) || !Number.isInteger(account.remainingWriteSlots)
    || account.remainingWriteSlots < writes * seconds) {
  throw new Error('전용 테스트 계정·냉장고·잔여 쓰기 용량을 확인하세요.');
}
const duration = new Trend('business_duration', true);
const failed = new Rate('business_failed');
const serverFailed = new Rate('business_5xx');
const count = new Counter('business_requests');
const thresholds = {
  dropped_iterations: ['count==0'],
  'business_requests{kind:read}': [`count==${reads * seconds}`],
  'business_requests{kind:write}': [`count==${writes * seconds}`],
  'business_duration{kind:read}': [],
  'business_duration{kind:write}': [],
};
// 이전 중 의도된 쓰기 차단은 원시 표본으로 평가한다. DNS 전환에서는 모든 업무 실패를 실패 처리한다.
if (mode === 'dns') thresholds.business_failed = ['rate==0'];
export const options = {
  scenarios: {
    reads: { executor: 'constant-arrival-rate', exec: 'read', rate: reads, timeUnit: '1s',
      duration: `${seconds}s`, preAllocatedVUs: 10, maxVUs: 30, gracefulStop: '20s' },
    writes: { executor: 'constant-arrival-rate', exec: 'write', rate: writes, timeUnit: '1s',
      duration: `${seconds}s`, preAllocatedVUs: 2, maxVUs: 10, gracefulStop: '20s' },
  },
  noConnectionReuse: true,
  dns: { ttl: '1s', select: 'random', policy: 'preferIPv4' },
  systemTags: ['name', 'method', 'status', 'scenario', 'error_code', 'expected_response'],
  thresholds,
  summaryTrendStats: ['avg', 'p(95)', 'p(99)', 'max'],
};
function params(name, extra = {}) {
  const csrf = http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0];
  return { redirects: 0, timeout: '10s', tags: { name }, headers: {
    Origin: base, 'Content-Type': 'application/json',
    ...(csrf ? { 'X-XSRF-TOKEN': decodeURIComponent(csrf) } : {}), ...extra,
  } };
}
function data(response) {
  try { return response.json().data; } catch (_) { return null; }
}
function must(ok, label) {
  if (!ok) throw new Error(`${label} 실패. 응답 본문은 출력하지 않습니다.`);
}
export function setup() {
  const csrf = http.get(`${base}/api/v1/auth/csrf`, params('setup_csrf'));
  must([200, 204].includes(csrf.status) && http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0], 'CSRF');
  const login = http.post(`${base}/api/v1/auth/sessions`, JSON.stringify({
    loginId: account.loginId, password: account.password,
  }), params('setup_login'));
  must(login.status === 200 && Array.isArray(data(login)?.activeRefrigeratorIds)
    && data(login).activeRefrigeratorIds.some(id => String(id) === String(account.refrigeratorId)), '테스트 냉장고 권한');
  const refreshed = http.get(`${base}/api/v1/auth/csrf`, params('setup_csrf_after_login'));
  const cookies = http.cookieJar().cookiesForURL(base);
  must([200, 204].includes(refreshed.status) && cookies.accessToken?.[0] && cookies['XSRF-TOKEN']?.[0], '로그인 쿠키');
  // k6 setup의 쿠키 저장소는 VU와 공유되지 않으므로 이 실행의 필요한 쿠키만 전달한다.
  return { accessToken: cookies.accessToken[0], csrf: cookies['XSRF-TOKEN'][0], fridge: String(account.refrigeratorId) };
}
function session(s) {
  http.cookieJar().set(base, 'accessToken', s.accessToken, { path: '/', secure: true });
  http.cookieJar().set(base, 'XSRF-TOKEN', s.csrf, { path: '/', secure: true });
}
function record(response, kind, ok, started, marker = '') {
  count.add(1, { kind }); failed.add(!ok, { kind });
  serverFailed.add(response.status >= 500, { kind });
  duration.add(response.timings.duration, { kind });
  check(ok, { [`${kind}_business_ok`]: value => value });
  console.log(JSON.stringify({ type: 'sample', runId, phase, kind, marker,
    startedMs: started, endedMs: Date.now(), status: response.status, errorCode: response.error_code || 0,
    ok, durationMs: response.timings.duration }));
}
export function read(s) {
  session(s);
  const started = Date.now();
  const response = http.get(`${base}/api/v1/refrigerators/${s.fridge}/ingredients?size=10&sort=CREATED_DESC`, params('ingredient_list'));
  record(response, 'read', response.status === 200 && Array.isArray(data(response)?.ingredients), started);
}
export function write(s) {
  session(s);
  const marker = `sw-${runId}-${phase}-${exec.scenario.iterationInTest}`;
  const date = new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 10);
  const started = Date.now();
  const response = http.post(`${base}/api/v1/refrigerators/${s.fridge}/ingredients`, JSON.stringify({ items: [{
    name: marker, category: 'OTHER', storageType: 'REFRIGERATED', measureType: 'COUNT',
    quantity: 1, weightValue: null, weightUnit: 'NONE', expirationDate: date,
  }] }), params('ingredient_create'));
  record(response, 'write', response.status === 201 && data(response)?.createdCount === 1
    && data(response)?.mergedCount === 0, started, marker);
}
export function handleSummary(summary) {
  const result = { runId, phase, mode, baseUrl: base, seconds, readRps: reads, writeRps: writes,
    markerPrefix: `sw-${runId}-${phase}-`, refrigeratorId: String(account.refrigeratorId),
    expectedRequests: { read: reads * seconds, write: writes * seconds },
    finishedAt: new Date().toISOString(), metrics: summary.metrics,
    scope: '읽기·고유 재료 쓰기. 생성 데이터 정리·DB 승격·DNS 변경은 수행하지 않음',
    caveat: 'DNS 가중치는 요청 비율을 보장하지 않음. OS·재귀 DNS 캐시는 별도 관찰',
  };
  return { [__ENV.RESULT_FILE]: JSON.stringify(result, null, 2), stdout: `측정 종료: ${runId}/${phase}\n` };
}

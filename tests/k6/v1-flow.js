import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Trend, Rate } from 'k6/metrics';

const base = 'https://dev.dameokja.com';
const modes = {
  smoke: { users: 2, sessions: 2, seconds: 10 },
  morning: { users: 140, sessions: 140, seconds: 300 },
  peak: { users: 200, sessions: 700, seconds: 300 },
  weeknight: { users: 100, sessions: 100, seconds: 3600 },
  weekend_lunch: { users: 150, sessions: 150, seconds: 3600 },
  weekend_dinner: { users: 150, sessions: 150, seconds: 3600 },
};
const mode = __ENV.MODE || 'smoke';
const config = modes[mode];
if (!config) throw new Error('지원하지 않는 MODE입니다.');
const accounts = new SharedArray('accounts', () => {
  const file = JSON.parse(open('./private/accounts.json'));
  if (file.baseUrl !== base || file.accounts.length !== 200) throw new Error('계정 파일 대상 또는 개수 오류');
  return file.accounts;
});
const completed = new Counter('journeys_completed');
const business = new Counter('business_requests');
const latency = new Trend('business_duration', true);
const failures = new Rate('business_failed');
const empty = new Counter('empty_inventory_responses');
const attempted = new Counter('journeys_attempted');
const journeyFailed = new Rate('journey_failed');
const allFailed = new Rate('all_requests_failed');
export const options = {
  noCookiesReset: true,
  scenarios: { journey: { executor: 'per-vu-iterations', vus: config.users,
    iterations: 1, maxDuration: `${config.seconds + 60}s` } },
  thresholds: {
    checks: ['rate==1'],
    journeys_completed: [`count==${config.sessions}`],
    business_requests: [`count==${config.sessions * 3}`],
    business_failed: ['rate<0.01'],
    all_requests_failed: ['rate<0.01'],
    business_duration: ['p(95)<200', 'max<1000'],
  },
};
function params(name, csrf) {
  return { timeout: '10s', redirects: 0, tags: { name }, headers: {
    Origin: base, 'Content-Type': 'application/json',
    ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}),
  } };
}
function validate(response, name, shape) {
  business.add(1);
  latency.add(response.timings.duration, { endpoint: name });
  let body;
  try { body = response.json().data; } catch (_) { body = null; }
  const ok = response.status === 200 && shape(body);
  failures.add(!ok);
  allFailed.add(!ok);
  check(ok, { [`${name} 응답 검증`]: v => v });
  if (!ok) throw new Error(`${name} 실패: HTTP ${response.status} 또는 응답 형식 오류`);
  return body;
}
export default function () {
  const index = exec.vu.idInTest - 1;
  const account = accounts[index];
  const start = exec.scenario.startTime;
  // 각 계정에 고정된 VU를 배정하고 세션 시작을 기간 전체에 균등 분산한다.
  for (let slot = index; slot < config.sessions; slot += config.users) {
    const wait = (start + slot * config.seconds * 1000 / config.sessions - Date.now()) / 1000;
    if (wait > 0) sleep(wait);
    attempted.add(1);
    try {
    const csrfResponse = http.get(`${base}/api/v1/auth/csrf`, params('csrf_auxiliary'));
    const token = http.cookieJar().cookiesForURL(`${base}/api/v1/auth/sessions`)['XSRF-TOKEN']?.[0];
    const csrfOk = [200, 204].includes(csrfResponse.status) && !!token;
    allFailed.add(!csrfOk);
    if (!csrfOk) throw new Error(`CSRF 실패: HTTP ${csrfResponse.status} 또는 쿠키 누락`);
    const auth = validate(http.post(`${base}/api/v1/auth/sessions`, JSON.stringify({
      loginId: account.loginId, password: account.password,
    }), params('login', decodeURIComponent(token))), 'login', d => !!d?.activeRefrigeratorIds?.[0]);
    const id = auth?.activeRefrigeratorIds?.[0];
    for (const sort of ['CREATED_DESC', 'EXPIRATION_ASC']) {
      const data = validate(http.get(`${base}/api/v1/refrigerators/${encodeURIComponent(id)}/ingredients?size=10&sort=${sort}`,
        params(sort)), sort, d => Array.isArray(d?.ingredients));
      if (!data.ingredients.length) empty.add(1);
    }
    completed.add(1);
    journeyFailed.add(false);
    } catch (error) {
      // 실패한 흐름만 종료하며 재시도로 예정 부하를 늘리지 않는다.
      journeyFailed.add(true);
      console.warn(`흐름 ${slot} 실패: ${error.message}`);
    }
  }
  // 마지막 세션 이후에도 측정 구간을 유지해 평균 처리율의 분모를 보존한다.
  const remaining = (start + config.seconds * 1000 - Date.now()) / 1000;
  if (remaining > 0) sleep(remaining);
}
export function handleSummary(data) {
  const result = { mode, config, finishedAt: new Date().toISOString(), metrics: data.metrics };
  return { [__ENV.RESULT_FILE || `./private/v1-${mode}.json`]: JSON.stringify(result, null, 2),
    stdout: JSON.stringify(result, null, 2) + '\n' };
}

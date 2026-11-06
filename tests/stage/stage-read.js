import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Rate, Trend } from 'k6/metrics';

const plan = JSON.parse(open('./plan.json'));
const base = __ENV.BASE_URL || plan.baseUrl;
if (base !== 'https://stage.dameokja.com' || plan.baseUrl !== base || plan.namespace !== 'stage') {
  throw new Error('이 스크립트는 https://stage.dameokja.com 전용입니다.');
}
const mode = __ENV.MODE || 'smoke';
if (!['smoke', 'hpa'].includes(mode)) throw new Error('MODE는 smoke 또는 hpa여야 합니다.');
const runId = __ENV.RUN_ID;
if (!runId || !/^[a-zA-Z0-9_-]{1,80}$/.test(runId)) throw new Error('유효한 RUN_ID가 필요합니다.');
const accountCount = mode === 'smoke' ? plan.smoke.vus : plan.hpa.maxVUs;
const accounts = new SharedArray('stage-accounts', () => {
  const data = JSON.parse(open(__ENV.ACCOUNTS_FILE || './private/accounts.json'));
  if (data.baseUrl !== base || data.accounts.length < accountCount) throw new Error('stage 전용 계정 수가 부족합니다.');
  const unique = new Set();
  for (const account of data.accounts) {
    if (!account.loginId || !account.password || !account.refrigeratorId ||
        Object.values(account).some(v => String(v).includes('REPLACE')) || unique.has(account.loginId)) {
      throw new Error('테스트 계정의 필수 값 또는 고유성이 잘못되었습니다.');
    }
    unique.add(account.loginId);
  }
  return data.accounts;
});
const failed = new Rate('business_failed');
const duration = new Trend('business_duration', true);
const requests = new Counter('business_requests');
const phases = new Counter('load_phase_observed');
const scenario = mode === 'smoke'
  ? { executor: 'per-vu-iterations', ...plan.smoke, gracefulStop: '10s' }
  : { executor: 'ramping-arrival-rate', startRate: plan.hpa.startRate, timeUnit: '1s',
      preAllocatedVUs: plan.hpa.preAllocatedVUs, maxVUs: plan.hpa.maxVUs,
      stages: plan.hpa.stages.map(({ duration, target }) => ({ duration, target })), gracefulStop: '15s' };
export const options = {
  noCookiesReset: true,
  tags: { run_id: runId, environment: 'stage', mode },
  scenarios: { stage_read: scenario },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  thresholds: {
    business_requests: ['count>0'],
    business_failed: [
      `rate<${plan.thresholds.errorRate}`,
      { threshold: `rate<${plan.thresholds.abortErrorRate}`, abortOnFail: true, delayAbortEval: '30s' },
    ],
    business_duration: [`p(95)<${plan.thresholds.p95Ms}`, `p(99)<${plan.thresholds.p99Ms}`],
    http_req_failed: [{ threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '30s' }],
    ...(mode === 'smoke' ? { checks: ['rate==1'] } : { dropped_iterations: ['count==0'] }),
  },
};

function params(name, csrf) {
  return { timeout: '10s', redirects: 0, tags: { name }, headers: {
    Origin: base, 'Content-Type': 'application/json', 'X-Load-Test-Run': runId,
    ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}),
  } };
}
function body(response) {
  try { return response.json().data; } catch (_) { return null; }
}
let authenticated = false;
let lastPhase = '';
function authenticate(account) {
  const csrf = http.get(`${base}/api/v1/auth/csrf`, params('csrf'));
  const token = http.cookieJar().cookiesForURL(`${base}/api/v1/auth/sessions`)['XSRF-TOKEN']?.[0];
  if (![200, 204].includes(csrf.status) || !token) exec.test.abort('stage CSRF 발급 실패');
  const login = http.post(`${base}/api/v1/auth/sessions`, JSON.stringify({
    loginId: account.loginId, password: account.password,
  }), params('login', decodeURIComponent(token)));
  if (login.status !== 200 || !(body(login)?.activeRefrigeratorIds || []).map(String).includes(String(account.refrigeratorId))) {
    // 인증정보·응답 본문은 출력하지 않습니다.
    exec.test.abort('stage 로그인 또는 지정 냉장고 접근 검증 실패');
  }
  authenticated = true;
}
function seconds(value) {
  const match = /^(\d+)(s|m)$/.exec(value);
  if (!match) throw new Error('duration은 정수 초 또는 분이어야 합니다.');
  return Number(match[1]) * (match[2] === 'm' ? 60 : 1);
}
function phase() {
  if (mode === 'smoke') return 'smoke';
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  let end = 0;
  for (const stage of plan.hpa.stages) {
    end += seconds(stage.duration);
    if (elapsed < end) return stage.name;
  }
  return 'graceful-stop';
}
export default function () {
  const current = phase();
  if (lastPhase !== current) {
    lastPhase = current;
    phases.add(1, { phase: current });
    console.log(JSON.stringify({ event: 'phase_observed', phase: current, run_id: runId, at: new Date().toISOString(), scenario_started_epoch_ms: exec.scenario.startTime }));
  }
  const account = accounts[exec.vu.idInTest - 1];
  if (!account) exec.test.abort('고유한 stage VU 계정이 부족합니다.');
  if (!authenticated) authenticate(account);
  // 사용자 데이터를 만들거나 삭제하지 않고 두 정렬의 목록 조회만 반복합니다.
  for (const sort of ['CREATED_DESC', 'EXPIRATION_ASC']) {
    const response = http.get(`${base}/api/v1/refrigerators/${encodeURIComponent(account.refrigeratorId)}/ingredients?size=10&sort=${sort}`,
      params(`ingredients_${sort}`));
    const ok = response.status === 200 && Array.isArray(body(response)?.ingredients);
    requests.add(1, { phase: current });
    failed.add(!ok, { phase: current });
    duration.add(response.timings.duration, { phase: current });
    check(ok, { '목록 조회 HTTP 200·응답 형식 정상': value => value });
  }
}
export function handleSummary(data) {
  const result = { run_id: runId, environment: 'stage', mode, baseUrl: base, plan,
    finishedAt: new Date().toISOString(), metrics: data.metrics };
  return { [__ENV.SUMMARY_FILE || './results/summary.json']: JSON.stringify(result, null, 2),
    stdout: JSON.stringify({ run_id: runId, mode, metrics: data.metrics }, null, 2) + '\n' };
}

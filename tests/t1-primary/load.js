// 9:1 업무 세션 비율을 HTTP 요청 수로 환산한다. 읽기 2회, 수정 4회로 총 95 HTTP RPS이다.
import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter, Rate, Trend } from 'k6/metrics';
const base = 'https://stage.dameokja.com';
const accounts = new SharedArray('accounts', () => JSON.parse(open('/work/accounts.json')));
const fixtures = new SharedArray('fixtures', () => JSON.parse(open('/work/fixtures.json')));
const sent = new Counter('business_sent');
const completed = new Counter('business_completed');
const latency = new Trend('business_latency', true);
const errors = new Rate('business_error');
const integrity = new Rate('integrity_error');
const authErrors = new Rate('auth_error');
const warmup = Number(__ENV.WARMUP || 300);
const seconds = Number(__ENV.SECONDS || 1800);
const scale = Number(__ENV.SCALE || 1);
const readRate = Math.round(855 * scale);
const writeRate = Math.round(95 * scale);
export const options = {
  setupTimeout: '3m',
  noCookiesReset: true, summaryTrendStats: ['avg','med','p(95)','p(99)','max'],
  scenarios: {
    read: { executor: 'constant-arrival-rate', exec: 'read', rate: readRate, timeUnit: '22s', duration: `${warmup+seconds}s`, preAllocatedVUs: 100, maxVUs: 100, gracefulStop: '15s' },
    write: { executor: 'constant-arrival-rate', exec: 'write', rate: writeRate, timeUnit: '22s', duration: `${warmup+seconds}s`, preAllocatedVUs: 50, maxVUs: 50, gracefulStop: '15s' },
  },
  thresholds: {
    dropped_iterations: ['count==0'],
    business_error: [{ threshold: 'rate==0', abortOnFail: true, delayAbortEval: '15s' }],
    integrity_error: [{ threshold: 'rate==0', abortOnFail: true, delayAbortEval: '15s' }],
    auth_error: [{ threshold: 'rate==0', abortOnFail: true, delayAbortEval: '15s' }],
    'business_latency{phase:measure}': ['p(95)<=1000'],
    business_latency: [{ threshold: 'p(95)<5000', abortOnFail: true, delayAbortEval: '30s' }],
  },
};
let logged = false;
let renewAt = 0;
let seq = 0;
const a = () => accounts[(exec.vu.idInTest-1)%50];
const f = () => fixtures[(exec.vu.idInTest-1)%50];
export function setup() {
  const sessions = [];
  for (let i = 0; i < 150; i++) {
    http.cookieJar().clear(base);
    const csrf = http.get(base+'/api/v1/auth/csrf', { tags: { kind:'auth', phase:'setup', name:'csrf' }, timeout:'10s' });
    const token = http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '';
    const login = http.post(base+'/api/v1/auth/sessions', JSON.stringify(accounts[i%50]), {
      tags:{kind:'auth',phase:'setup',name:'login'}, timeout:'10s', redirects:0,
      headers:{Origin:base,'Content-Type':'application/json','X-XSRF-TOKEN':decodeURIComponent(token)},
    });
    const bad = csrf.status !== 204 || login.status !== 200 || Number(login.json('data.activeRefrigeratorIds.0')) !== Number(fixtures[i%50].fridge);
    authErrors.add(bad,{phase:'setup'});
    if (bad) exec.test.abort('사전 인증 실패');
    sessions.push(Object.entries({...csrf.cookies,...login.cookies}).flatMap(([name,values]) => values.map(cookie => ({...cookie,name}))));
  }
  return sessions;
}
function phase() {
  const elapsed = (Date.now()-exec.scenario.startTime)/1000;
  return elapsed < warmup ? 'warmup' : elapsed < warmup+seconds ? 'measure' : 'drain';
}
function req(method, path, body, api, headers = {}, auth = false) {
  const p = phase();
  const start = Date.now();
  const tags = { api, phase: p, kind: auth ? 'auth' : 'business', name: api,
    window_start: String(exec.scenario.startTime + warmup * 1000) };
  const token = http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '';
  if (!auth) sent.add(1, tags);
  const r = http.request(method, base + path, body === null ? null : JSON.stringify(body), {
    timeout: '10s', redirects: 0, tags,
    headers: { Origin: base, 'Content-Type': 'application/json', 'X-XSRF-TOKEN': decodeURIComponent(token), ...headers },
  });
  const bad = r.status < 200 || r.status >= 300;
  if (auth) authErrors.add(bad, tags);
  else {
    completed.add(1, { ...tags, status: String(r.status) });
    latency.add(Date.now()-start, { ...tags, status: String(r.status) });
    errors.add(bad, { ...tags, status: String(r.status) });
  }
  if (bad) exec.test.abort(`${api}: HTTP ${r.status}`);
  return r;
}
function login(sessions) {
  if (!logged) {
    for (const cookie of sessions[exec.vu.idInTest-1]) {
      http.cookieJar().set(base,cookie.name,cookie.value,{path:cookie.path || '/',secure:cookie.secure});
    }
    logged = true;
    renewAt = Date.now() + 600000 + exec.vu.idInTest * 500;
  } else if (Date.now() >= renewAt) {
    req('POST', '/api/v1/auth/token-renewals', null, 'renew', {}, true);
    renewAt = Date.now() + 600000;
  }
}
export function read(sessions) {
  login(sessions);
  const path = `/api/v1/refrigerators/${f().fridge}/ingredients`;
  const sort = ['EXPIRATION_ASC','CREATED_DESC','NAME_ASC'][seq++%3];
  const first = req('GET', `${path}?size=50&sort=${sort}`, null, 'list');
  const cursor = first.json('data.nextCursor');
  const firstItems = first.json('data.ingredients') || [];
  const bad = Number(first.json('data.ingredientsNum')) !== 1000 || !cursor || firstItems.length !== 50;
  integrity.add(bad, { phase: phase(), api: 'list' });
  if (bad) exec.test.abort('목록 데이터 정합성 실패');
  const next = req('GET', `${path}?size=50&sort=${sort}&cursor=${encodeURIComponent(cursor)}`, null, 'list-next');
  const firstIds = new Set(firstItems.map(item => String(item.ingredientId)));
  const nextItems = next.json('data.ingredients') || [];
  const pageBad = nextItems.length !== 50 || nextItems.some(item => firstIds.has(String(item.ingredientId)));
  integrity.add(pageBad, { phase: phase(), api:'list-next' });
  if (pageBad) exec.test.abort('다음 페이지 건수 또는 중복 검증 실패');
}
export function write(sessions) {
  login(sessions);
  const fixture = f();
  // 같은 냉장고를 쓰는 VU끼리도 수정 행은 겹치지 않는다.
  const id = fixture.ids[Math.floor((exec.vu.idInTest-1)/50)];
  req('GET', `/api/v1/refrigerators/${fixture.fridge}/ingredients?size=20&sort=CREATED_DESC`, null, 'write-pick');
  const detail = req('GET', `/api/v1/ingredients/${id}`, null, 'detail');
  const quantity = Number(detail.json('data.quantity')) === 1 ? 2 : 1;
  req('PATCH', `/api/v1/ingredients/${id}`, { quantity }, 'update', { 'If-Match': detail.headers.Etag });
  const verify = req('GET', `/api/v1/ingredients/${id}`, null, 'verify');
  const bad = Number(verify.json('data.quantity')) !== quantity;
  integrity.add(bad, { phase: phase(), api: 'update' });
  if (bad) exec.test.abort('쓰기 후 재조회 불일치');
}
export function handleSummary(data) {
  return { [__ENV.SUMMARY || '/work/summary.json']: JSON.stringify(data, null, 2) };
}

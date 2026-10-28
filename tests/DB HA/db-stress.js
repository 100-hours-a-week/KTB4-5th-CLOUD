// DB 한계 부하: 요청마다 DB를 많이 쓰는 API를 읽기·쓰기로 섞어 DB가 먼저 버티지 못하는 지점을 찾는다. 설계는 README.md 참고.
// 실행: 먼저 db-reset.js로 시험 냉장고를 같은 상태로 맞춘 뒤
//   k6 run -e ACCOUNTS=../k6/private/prod-accounts.json --out csv=private/db-stress.csv db-stress.js
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Rate, Counter } from 'k6/metrics';

const base = __ENV.BASE_URL || 'https://dameokja.com';
const start = Number(__ENV.START || 50);
const step = Number(__ENV.STEP || 25);
const max = Number(__ENV.MAX || 400);
const hold = Number(__ENV.STEP_SECONDS || 120);
const maxVUs = Number(__ENV.MAX_VUS || 300);
const accounts = new SharedArray('accounts', () => {
  const file = JSON.parse(open(__ENV.ACCOUNTS));
  if (file.baseUrl !== base) throw new Error(`계정 파일 대상(${file.baseUrl})이 BASE_URL과 다릅니다.`);
  return file.accounts;
});
const failed = new Rate('flow_failed');
const full = new Counter('fridge_full');
const stale = new Counter('stale_version');

const stages = [];
for (let rps = start; rps <= max; rps += step) stages.push({ target: rps, duration: '10s' }, { target: rps, duration: `${hold - 10}s` });
export const options = {
  noCookiesReset: true,
  scenarios: { db: { executor: 'ramping-arrival-rate', startRate: start, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs, stages } },
  // 한계를 넘긴 뒤의 모습도 보려고 ramp.js(10%)보다 느슨하게 둔다. 누적 실패율 30%를 넘으면 자동 중단한다.
  thresholds: { flow_failed: [{ threshold: 'rate<0.3', abortOnFail: true, delayAbortEval: '2m' }] },
};

function params(name, csrf) {
  const s = Math.min(Math.floor((Date.now() - exec.scenario.startTime) / 1000 / hold), stages.length / 2 - 1);
  return { timeout: '30s', redirects: 0, tags: { name, step: String(start + s * step) },
    headers: { Origin: base, 'Content-Type': 'application/json', ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}) } };
}
const csrf = () => decodeURIComponent(http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '');
const pick = (list) => list[Math.floor(Math.random() * list.length)];

// 로그인·갱신은 ramp.js와 같다(VU당 로그인 1회, 10~14분 사이 refresh token 갱신).
let fridge = null;
let renewAt = 0;
let logged = false;
let seq = 0;
const nextRenewal = () => Date.now() + (10 + Math.random() * 4) * 60000;
function login(account) {
  http.get(`${base}/api/v1/auth/csrf`, params('csrf'));
  const res = http.post(`${base}/api/v1/auth/sessions`,
    JSON.stringify({ loginId: account.loginId, password: account.password }), params('login', csrf()));
  fridge = res.status === 200 ? res.json('data.activeRefrigeratorIds.0') : null;
  renewAt = nextRenewal();
}
function renew() {
  const res = http.post(`${base}/api/v1/auth/token-renewals`, null, params('renew', csrf()));
  if (res.status === 200) renewAt = nextRenewal();
  else fridge = null;
}

const SORTS = ['EXPIRATION_ASC', 'CREATED_DESC', 'NAME_ASC'];
const FILTERS = ['NORMAL', 'EXPIRING_SOON', 'EXPIRED', 'REFRIGERATED', 'FROZEN'];
const CATEGORIES = ['VEGETABLE', 'FRUIT', 'MEAT', 'SEAFOOD', 'DAIRY', 'OTHER'];

// 목록: 한 페이지 최대 50개 + 전체 개수(count) + 정렬. 검색어는 앞뒤 % LIKE라 인덱스를 못 탄다. 30%는 다음 페이지까지 읽는다.
function list(path) {
  let q = `size=50&sort=${pick(SORTS)}`;
  if (Math.random() < 0.5) q += `&keyword=${pick(['hatest', 'st-', '1', '2', '3'])}`;
  if (Math.random() < 0.3) q += `&filter=${pick(FILTERS)}`;
  if (Math.random() < 0.3) q += `&category=${pick(CATEGORIES)}`;
  let res = http.get(`${path}?${q}`, params('list'));
  const cursor = res.status === 200 ? res.json('data.nextCursor') : null;
  if (cursor && Math.random() < 0.3) res = http.get(`${path}?${q}&cursor=${encodeURIComponent(cursor)}`, params('list-next'));
  return res;
}

// 쓰기는 모두 실제로 행을 바꾼다(2026-10-28 수정). 전에는 냉장고가 가득 차 등록이 409로 끝나고,
// 일괄 폐기 API는 유통기한이 지난 항목만 지워 실제 쓰기가 거의 없었다.
// 등록: 이름이 다른 항목 1개 INSERT. 냉장고(1,500개)가 차면 409(fridge_full로 셈, 실패 아님)
function create(path) {
  // 유통기한은 오늘부터 4년 이내만 허용된다. 1~1,200일 뒤로 흩어 정렬·필터 대상이 고르게 섞이게 한다.
  const expiry = new Date(Date.now() + (1 + Math.floor(Math.random() * 1200)) * 86400000).toISOString().slice(0, 10);
  const res = http.post(path, JSON.stringify({ items: [{ name: `hatest-${exec.vu.idInTest}-${seq++}`, category: pick(CATEGORIES),
    storageType: Math.random() < 0.7 ? 'REFRIGERATED' : 'FROZEN', measureType: 'COUNT', quantity: 1,
    weightValue: null, weightUnit: 'NONE', expirationDate: expiry, imageUploadId: null }] }), params('create', csrf()));
  if (res.status === 409) full.add(1);
  return res;
}

// 시험 항목 하나를 골라 상세(ETag)까지 읽는다. 같은 냉장고를 VU 여러 개가 쓰므로 그사이 바뀌면 412가 난다(stale로 셈, 실패 아님)
function pickDetail(path) {
  const found = http.get(`${path}?size=20&sort=${pick(SORTS)}&keyword=hatest-`, params('pick'));
  const items = found.status === 200 ? (found.json('data.ingredients') || []) : [];
  if (items.length === 0) return null;
  const res = http.get(`${base}/api/v1/ingredients/${pick(items).ingredientId}`, params('detail'));
  return res.status === 200 ? res : null;
}

function withEtag(p, detail) {
  p.headers['If-Match'] = detail.headers.Etag;
  return p;
}

// 소비: 수량 전부를 소비해 행을 DELETE한다(냉장고 잠금 + 월별 집계 갱신)
function consume(path) {
  const d = pickDetail(path);
  if (!d) return null;
  return http.post(`${base}/api/v1/ingredients/${d.json('data.ingredientId')}`,
    JSON.stringify({ quantity: d.json('data.quantity'), weightValue: null }),
    withEtag(params('consume', csrf()), d));
}

// 수정: 수량을 바꿔 UPDATE한다
function update(path) {
  const d = pickDetail(path);
  if (!d) return null;
  return http.patch(`${base}/api/v1/ingredients/${d.json('data.ingredientId')}`,
    JSON.stringify({ quantity: 1 + Math.floor(Math.random() * 50) }),
    withEtag(params('update', csrf()), d));
}

export default function () {
  const account = accounts[(exec.vu.idInTest - 1) % accounts.length];
  if (fridge && Date.now() > renewAt) renew();
  if (!fridge) login(account);
  if (!fridge) { failed.add(true); return; }
  const path = `${base}/api/v1/refrigerators/${fridge}/ingredients`;
  // 읽기 45%(목록 35, 마이페이지 10) / 쓰기 55%(등록 17.5, 소비 17.5, 수정 20). 등록과 소비를 같게 둬 측정 중에도 항목 수가 거의 그대로다
  const r = Math.random();
  let res;
  if (r < 0.35) res = list(path);
  else if (r < 0.45) res = http.get(`${base}/api/v1/refrigerators/${fridge}/mypage`, params('mypage'));
  else if (r < 0.625) res = create(path);
  else if (r < 0.80) res = consume(path);
  else res = update(path);
  if (!res) { failed.add(false); return; }
  if (res.status === 401) renew();
  if (res.status === 412) stale.add(1);
  // 409(냉장고 가득)·412(다른 VU가 먼저 바꿈)는 정상 응답으로 본다
  const ok = check(res, { ok: (x) => (x.status >= 200 && x.status < 300) || x.status === 409 || x.status === 412 });
  if (!ok && !logged) {
    logged = true;
    let body = {};
    try { body = res.json(); } catch (_) {}
    console.warn(`${res.request.method} ${res.status} code=${body.code} message=${body.message} errors=${JSON.stringify(body.errors || [])}`);
  }
  failed.add(!ok);
}

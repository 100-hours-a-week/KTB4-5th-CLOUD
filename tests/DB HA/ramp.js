// T1 점진 부하: START RPS에서 STEP_SECONDS마다 STEP씩 올린다. 설계는 README.md 참고.
// 실행: k6 run -e ACCOUNTS=../k6/private/prod-accounts.json --out csv=private/ramp.csv ramp.js
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Rate } from 'k6/metrics';

const base = __ENV.BASE_URL || 'https://dameokja.com';
const start = Number(__ENV.START || 10);
const step = Number(__ENV.STEP || 20);
const max = Number(__ENV.MAX || 300);
const hold = Number(__ENV.STEP_SECONDS || 180);
const writeRatio = Number(__ENV.WRITE_RATIO || 0.2);
// 응답이 느려질 때 k6가 VU를 계속 늘리면 새 VU마다 로그인해 부하가 커지므로 상한을 둔다. 모자라면 dropped_iterations로 남는다.
const maxVUs = Number(__ENV.MAX_VUS || 300);
const accounts = new SharedArray('accounts', () => {
  const file = JSON.parse(open(__ENV.ACCOUNTS));
  if (file.baseUrl !== base) throw new Error(`계정 파일 대상(${file.baseUrl})이 BASE_URL과 다릅니다.`);
  return file.accounts;
});
const failed = new Rate('flow_failed');

const stages = [];
for (let rps = start; rps <= max; rps += step) stages.push({ target: rps, duration: '10s' }, { target: rps, duration: `${hold - 10}s` });
export const options = {
  noCookiesReset: true,
  scenarios: { ramp: { executor: 'ramping-arrival-rate', startRate: start, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs, stages } },
  // 누적 실패율이 10%를 넘으면 자동 중단한다. 그 전에 회복되지 않는 단계는 사람이 Ctrl+C로 멈춘다.
  thresholds: { flow_failed: [{ threshold: 'rate<0.1', abortOnFail: true, delayAbortEval: '1m' }] },
};

function params(name, csrf) {
  // step 태그로 단계별 지표를 나눠 본다.
  const s = Math.min(Math.floor((Date.now() - exec.scenario.startTime) / 1000 / hold), stages.length / 2 - 1);
  return { timeout: '30s', redirects: 0, tags: { name, step: String(start + s * step) },
    headers: { Origin: base, 'Content-Type': 'application/json', ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}) } };
}
const csrf = () => decodeURIComponent(http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '');

let fridge = null;
let renewAt = 0;
let logged = false;
// 실제 사용자처럼 VU마다 처음 한 번만 로그인하고, 이후에는 refresh token으로 갱신한다(로그인 bcrypt를 반복하지 않음).
// access token 15분 만료가 모든 VU에서 같은 순간에 오지 않도록 10~14분 사이 임의 시각에 미리 갱신한다.
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

export default function () {
  const account = accounts[(exec.vu.idInTest - 1) % accounts.length];
  if (fridge && Date.now() > renewAt) renew();
  if (!fridge) login(account);
  if (!fridge) { failed.add(true); return; }
  const path = `${base}/api/v1/refrigerators/${fridge}/ingredients`;
  let res;
  if (Math.random() < writeRatio) {
    // 같은 이름·유통기한이면 기존 항목에 수량이 합쳐진다(UPDATE). 항목당 수량 상한이 100이라 유통기한을 60가지로 나눈다. 유통기한은 오늘부터 4년 이내만 허용된다.
    const expiry = new Date(Date.UTC(2029, 0, 1 + Math.floor(Math.random() * 60))).toISOString().slice(0, 10);
    res = http.post(path, JSON.stringify({ items: [{ name: 'hatest', category: 'OTHER', storageType: 'REFRIGERATED',
      measureType: 'COUNT', quantity: 1, weightValue: null, weightUnit: 'NONE', expirationDate: expiry,
      imageUploadId: null }] }), params('write', csrf()));
  } else {
    res = http.get(`${path}?size=10&sort=${Math.random() < 0.5 ? 'CREATED_DESC' : 'EXPIRATION_ASC'}`, params('read'));
  }
  if (res.status === 401) renew();
  const ok = check(res, { ok: (r) => r.status === 200 || r.status === 201 });
  // 실패 원인 확인용으로 VU마다 처음 실패 1건의 오류 코드만 남긴다(본문 전체는 남기지 않는다).
  if (!ok && !logged) {
    logged = true;
    let body = {};
    try { body = res.json(); } catch (_) {}
    console.warn(`${res.request.method} ${res.status} code=${body.code} message=${body.message} errors=${JSON.stringify(body.errors || [])}`);
  }
  failed.add(!ok);
}

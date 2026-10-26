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
    preAllocatedVUs: 50, maxVUs: 1000, stages } },
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
function login(account) {
  http.get(`${base}/api/v1/auth/csrf`, params('csrf'));
  const res = http.post(`${base}/api/v1/auth/sessions`,
    JSON.stringify({ loginId: account.loginId, password: account.password }), params('login', csrf()));
  fridge = res.status === 200 ? res.json('data.activeRefrigeratorIds.0') : null;
}

export default function () {
  const account = accounts[(exec.vu.idInTest - 1) % accounts.length];
  if (!fridge) login(account);
  if (!fridge) { failed.add(true); return; }
  const path = `${base}/api/v1/refrigerators/${fridge}/ingredients`;
  let res;
  if (Math.random() < writeRatio) {
    // 같은 이름·유통기한이면 기존 항목에 수량이 합쳐져(UPDATE) 냉장고 한도를 채우지 않는다.
    res = http.post(path, JSON.stringify({ items: [{ name: 'hatest', category: 'OTHER', storageType: 'REFRIGERATED',
      measureType: 'COUNT', quantity: 1, weightValue: null, weightUnit: 'NONE', expirationDate: '2099-12-31',
      registrationSource: 'DIRECT', imageUploadId: null }] }), params('write', csrf()));
  } else {
    res = http.get(`${path}?size=10&sort=${Math.random() < 0.5 ? 'CREATED_DESC' : 'EXPIRATION_ASC'}`, params('read'));
  }
  if (res.status === 401) fridge = null;
  failed.add(!check(res, { ok: (r) => r.status === 200 || r.status === 201 }));
}

// 전용 시험 계정만 생성하고 회차 사이에는 시험 재료의 수량만 초기화한다.
import http from 'k6/http';
import { fail } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Counter } from 'k6/metrics';
const base = 'https://stage.dameokja.com';
const accounts = new SharedArray('accounts', () => JSON.parse(open('/work/accounts.json')));
const ready = new Counter('fixtures_ready');
const count = Number(__ENV.COUNT || 50);
export const options = { scenarios: { prepare: { executor: 'shared-iterations', vus: 1, iterations: count, maxDuration: '40m' } }, noCookiesReset: true, thresholds: { fixtures_ready: [`count==${count}`] } };
function call(method, path, body, extra = {}) {
  const token = http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '';
  const r = http.request(method, base + path, body === null ? null : JSON.stringify(body), {
    timeout: '30s', redirects: 0, headers: { Origin: base, 'Content-Type': 'application/json', 'X-XSRF-TOKEN': decodeURIComponent(token), ...extra },
  });
  if (r.status < 200 || r.status >= 300) fail(`${method} ${path}: ${r.status} ${r.body?.slice(0, 200)}`);
  return r;
}
export default function () {
  const a = accounts[Number(__ENV.OFFSET || 0) + exec.scenario.iterationInTest];
  http.cookieJar().clear(base);
  call('GET', '/api/v1/auth/csrf', null);
  if (__ENV.MODE === 'create') call('POST', '/api/v1/users', { loginId: a.loginId, password: a.password, nickname: a.loginId });
  const session = call('POST', '/api/v1/auth/sessions', { loginId: a.loginId, password: a.password });
  const fridge = session.json('data.activeRefrigeratorIds.0');
  if (!fridge) fail('시험 냉장고 식별 실패');
  const path = `/api/v1/refrigerators/${fridge}/ingredients`;
  if (__ENV.MODE === 'create' || __ENV.MODE === 'resume') {
    const initial = call('GET', `${path}?size=1`, null);
    const count = Number(initial.json('data.ingredientsNum'));
    if (!Number.isInteger(count) || count > 1000 || count % 20) fail(`재개 건수 불일치: ${a.loginId}, ${count}`);
    for (let i = count; i < 1000; i += 20) {
      const items = Array.from({ length: 20 }, (_, n) => ({ name: `t1-${String(i+n).padStart(4,'0')}`, category: ['VEGETABLE','FRUIT','MEAT','SEAFOOD','DAIRY','OTHER'][(i+n)%6], storageType: (i+n)%10 < 7 ? 'REFRIGERATED' : 'FROZEN', measureType: 'COUNT', quantity: 1, weightValue: null, weightUnit: 'NONE', expirationDate: '2027-06-01', imageUploadId: null }));
      call('POST', path, { items });
    }
  }
  const list = call('GET', `${path}?size=50&sort=CREATED_DESC`, null);
  if (Number(list.json('data.ingredientsNum')) !== 1000) fail(`초기 건수 불일치: ${fridge}`);
  const ids = list.json('data.ingredients').map(x => x.ingredientId);
  if (ids.length !== 50) fail('수정 대상 50개 확보 실패');
  if (__ENV.MODE === 'reset') {
    for (const id of ids.slice(0, 3)) {
      const d = call('GET', `/api/v1/ingredients/${id}`, null);
      call('PATCH', `/api/v1/ingredients/${id}`, { quantity: 1 }, { 'If-Match': d.headers.Etag });
    }
  }
  console.log('FIXTURE ' + JSON.stringify({ loginId: a.loginId, fridge, ids }));
  ready.add(1);
}

// db-stress.js 측정 전에 시험 냉장고를 모두 같은 상태로 맞춘다: 항목을 전부 지우고 같은 내용의 항목 ITEMS개를 다시 넣는다.
// 실행: k6 run -e ACCOUNTS=../k6/private/prod-accounts.json db-reset.js  (계정 하나를 VU 하나가 맡는다)
import http from 'k6/http';
import { check, fail } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';

const base = __ENV.BASE_URL || 'https://dameokja.com';
const items = Number(__ENV.ITEMS || 1000);
const accounts = new SharedArray('accounts', () => {
  const file = JSON.parse(open(__ENV.ACCOUNTS));
  if (file.baseUrl !== base) throw new Error(`계정 파일 대상(${file.baseUrl})이 BASE_URL과 다릅니다.`);
  return file.accounts;
});
export const options = {
  noCookiesReset: true,
  scenarios: { reset: { executor: 'per-vu-iterations', vus: accounts.length, iterations: 1, maxDuration: '30m' } },
  thresholds: { checks: ['rate==1'] },
};

const params = (name, csrf, extra = {}) => ({ timeout: '30s', redirects: 0, tags: { name },
  headers: { Origin: base, 'Content-Type': 'application/json', ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}), ...extra } });
const csrf = () => decodeURIComponent(http.cookieJar().cookiesForURL(base)['XSRF-TOKEN']?.[0] || '');
const CATEGORIES = ['VEGETABLE', 'FRUIT', 'MEAT', 'SEAFOOD', 'DAIRY', 'OTHER'];

// i번째 항목은 어느 냉장고에서나 같다. 유통기한만 실행한 날 기준(1~1,200일 뒤)이라 같은 날 잰 측정끼리 같다
function item(i) {
  const expiry = new Date(Date.now() + (1 + (i * 37) % 1200) * 86400000).toISOString().slice(0, 10);
  return { name: `hatest-${i}`, category: CATEGORIES[i % CATEGORIES.length], storageType: i % 10 < 7 ? 'REFRIGERATED' : 'FROZEN',
    measureType: 'COUNT', quantity: 1 + (i % 50), weightValue: null, weightUnit: 'NONE', expirationDate: expiry, imageUploadId: null };
}

export default function () {
  const account = accounts[exec.vu.idInTest - 1];
  http.get(`${base}/api/v1/auth/csrf`, params('csrf'));
  const login = http.post(`${base}/api/v1/auth/sessions`,
    JSON.stringify({ loginId: account.loginId, password: account.password }), params('login', csrf()));
  if (login.status !== 200) fail(`로그인 실패 ${login.status}`);
  const path = `${base}/api/v1/refrigerators/${login.json('data.activeRefrigeratorIds.0')}/ingredients`;

  // 1) 전부 지우기: id를 먼저 다 모은 뒤 하나씩 수량 전부를 소비한다(일괄 폐기 API는 유통기한 지난 항목만 지운다)
  const ids = [];
  let cursor = null;
  do {
    const res = http.get(`${path}?size=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, params('list'));
    if (res.status !== 200) fail(`목록 실패 ${res.status}`);
    ids.push(...res.json('data.ingredients').map((x) => x.ingredientId));
    cursor = res.json('data.nextCursor');
  } while (cursor);
  for (const id of ids) {
    const d = http.get(`${base}/api/v1/ingredients/${id}`, params('detail'));
    if (d.status === 404) continue;
    http.post(`${base}/api/v1/ingredients/${id}`,
      JSON.stringify({ quantity: d.json('data.quantity'), weightValue: d.json('data.weightValue') }),
      params('consume', csrf(), { 'If-Match': d.headers.Etag }));
  }

  // 2) 같은 항목 ITEMS개 넣기(요청당 최대 20개)
  for (let i = 0; i < items; i += 20) {
    const batch = [];
    for (let j = i; j < Math.min(i + 20, items); j++) batch.push(item(j));
    http.post(path, JSON.stringify({ items: batch }), params('create', csrf()));
  }

  // 3) 확인: 항목 수가 정확히 ITEMS개여야 한다
  const count = http.get(`${path}?size=1`, params('count')).json('data.ingredientsNum');
  if (!check(count, { [`항목 ${items}개`]: (n) => n === items })) console.warn(`${account.loginId}: 항목 ${count}개 (지운 대상 ${ids.length}개)`);
}

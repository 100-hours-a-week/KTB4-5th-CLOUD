import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

const base = 'https://dev.dameokja.com';
const load = __ENV.MODE === 'load';
const seconds = Number(__ENV.DURATION_SECONDS || 30);
if (!Number.isInteger(seconds) || seconds < 2 || seconds > 30) throw new Error('DURATION_SECONDS는 2~30 정수여야 합니다.');
const count = load ? Math.ceil(3.5 * seconds) : 3;
export const options = {
  noCookiesReset: true,
  scenarios: {
    ingredients: load
      ? { executor: 'constant-arrival-rate', rate: 7, timeUnit: '2s', duration: `${seconds}s`, preAllocatedVUs: 10, maxVUs: 200 }
      : { executor: 'shared-iterations', vus: 1, iterations: 3, maxDuration: '1m' },
  },
  thresholds: {
    checks: ['rate==1'],
    http_req_failed: [{ threshold: 'rate==0', abortOnFail: true }],
    ...(load ? { dropped_iterations: ['count==0'] } : {}),
  },
};

function json(response) {
  try { return response.json(); } catch (_) { return {}; }
}

function requireResult(response, status, label) {
  if (!check(response, { [label]: (r) => r.status === status })) {
    // 인증 정보와 응답 본문을 로그에 출력하지 않는다.
    exec.test.abort(`${label}: HTTP ${response.status}, code=${json(response).code || 'unknown'}`);
  }
}

function params(name, csrf) {
  return {
    timeout: '10s', redirects: 0,
    tags: { name },
    headers: { 'Content-Type': 'application/json', Origin: base, ...(csrf ? { 'X-XSRF-TOKEN': csrf } : {}) },
  };
}

export function setup() {
  if (!__ENV.TEST_LOGIN_ID || !__ENV.TEST_PASSWORD) {
    throw new Error('TEST_LOGIN_ID와 TEST_PASSWORD 환경변수를 지정하세요.');
  }
  const csrfResponse = http.get(`${base}/api/v1/auth/csrf`, params('csrf'));
  if (![200, 204].includes(csrfResponse.status)) exec.test.abort(`CSRF 발급 실패: ${csrfResponse.status}`);
  const before = http.cookieJar().cookiesForURL(`${base}/api/v1/auth/sessions`);
  if (!before['XSRF-TOKEN']?.[0]) exec.test.abort('CSRF 쿠키가 없습니다.');
  const login = http.post(`${base}/api/v1/auth/sessions`, JSON.stringify({
    loginId: __ENV.TEST_LOGIN_ID, password: __ENV.TEST_PASSWORD,
  }), params('login', decodeURIComponent(before['XSRF-TOKEN'][0])));
  requireResult(login, 200, '로그인 성공');
  const ids = json(login).data?.activeRefrigeratorIds || [];
  const fridge = __ENV.REFRIGERATOR_ID || (ids.length === 1 ? ids[0] : null);
  if (!fridge || !ids.map(String).includes(String(fridge))) {
    exec.test.abort('냉장고가 여러 개이거나 없습니다. 접근 가능한 REFRIGERATOR_ID를 지정하세요.');
  }
  const path = `/api/v1/refrigerators/${encodeURIComponent(fridge)}/ingredients`;
  const cookies = http.cookieJar().cookiesForURL(`${base}${path}`);
  if (!cookies.accessToken?.[0] || !cookies['XSRF-TOKEN']?.[0]) exec.test.abort('로그인 후 인증 쿠키가 없습니다.');
  const baseline = http.get(`${base}${path}?size=10&sort=EXPIRATION_ASC`, params('ingredients_list'));
  requireResult(baseline, 200, '기존 목록 조회 성공');
  const data = json(baseline).data;
  if (!data || !Number.isFinite(Number(data.refrigeratorCapacity)) ||
      !Number.isFinite(Number(data.ingredientsNum)) ||
      Number(data.refrigeratorCapacity) - Number(data.ingredientsNum) < count) {
    exec.test.abort(`${count}개 생성 공간 부족 또는 응답 형식 불일치: 현재 ${data?.ingredientsNum}, 한도 ${data?.refrigeratorCapacity}`);
  }
  console.log(`냉장고 ${fridge}: 최대 ${count}개 생성 예정. 테스트 데이터는 자동 삭제하지 않습니다.`);
  return { path, cookies, run: Date.now().toString(36) };
}

let initialized = false;
export default function (data) {
  const jar = http.cookieJar();
  if (!initialized) {
    // 로그인에서 받은 쿠키를 각 가상 사용자의 쿠키 저장소에 복원한다.
    for (const [name, values] of Object.entries(data.cookies)) {
      jar.set(base, name, values[0], { path: '/', secure: true });
    }
    initialized = true;
  }
  const name = `k6-${data.run}-${exec.scenario.iterationInTest}`;
  const expiry = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
  const csrf = decodeURIComponent(jar.cookiesForURL(`${base}${data.path}`)['XSRF-TOKEN'][0]);
  const created = http.post(`${base}${data.path}`, JSON.stringify({ items: [{
    name, category: 'OTHER', storageType: 'REFRIGERATED', measureType: 'COUNT',
    quantity: 1, weightValue: null, weightUnit: 'NONE', expirationDate: expiry,
    registrationSource: 'DIRECT', imageUploadId: null,
  }] }), params('ingredients_create', csrf));
  requireResult(created, 201, '식재료 생성 성공');
  if (!check(json(created), { '새 항목 1개 생성': (body) => body.data?.createdCount === 1 })) {
    exec.test.abort('신규 생성 결과가 예상과 다릅니다.');
  }
  const listed = http.get(`${base}${data.path}?size=10&sort=EXPIRATION_ASC`, params('ingredients_list'));
  requireResult(listed, 200, '생성 후 목록 조회 성공');
  if (!check(json(listed), { '목록 응답 형식 정상': (body) => Array.isArray(body.data?.ingredients) })) {
    exec.test.abort('목록 응답 형식이 예상과 다릅니다.');
  }
  // 생성 항목은 정렬 및 페이지 위치에 따라 첫 페이지에 없을 수 있다.
  if (!load) sleep(1);
}

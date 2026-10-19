// 네트워크 요청 없이 k6의 쿠키 전달·쓰기 표식·응답 판정을 모의 검증한다.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(`${__dirname}/load.js`, 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '')
  + '\nglobalThis.testApi={setup,read,write,handleSummary};';
function fixture(overrides = {}, accountOverrides = {}) {
  const cookies = {}; const requests = []; const messages = [];
  let merged = false;
  const account = { baseUrl: 'https://dameokja.com', testOnly: true, loginId: 'offline-user',
    password: 'offline-not-a-real-secret', refrigeratorId: '123', remainingWriteSlots: 1000, ...accountOverrides };
  const response = (status, data = {}) => ({ status, timings: { duration: 100 }, json: () => ({ data }) });
  const http = {
    cookieJar: () => ({ cookiesForURL: () => cookies, set: (_, key, value) => { cookies[key] = [value]; } }),
    get: (url) => {
      if (url.endsWith('/auth/csrf')) { cookies['XSRF-TOKEN'] = ['offline-csrf']; return response(204); }
      return response(200, { ingredients: [] });
    },
    post: (url, body, params) => {
      requests.push({ url, body: JSON.parse(body), params });
      if (url.endsWith('/auth/sessions')) {
        cookies.accessToken = ['offline-access']; return response(200, { activeRefrigeratorIds: [123] });
      }
      return response(201, { createdCount: merged ? 0 : 1, mergedCount: merged ? 1 : 0 });
    },
  };
  const metric = function () { this.add = () => {}; };
  const context = { __ENV: { BASE_URL: 'https://dameokja.com', PROD_CONFIRMED: 'true',
    RUN_ID: 'offline1', PHASE: 'baseline', ACCOUNT_FILE: '/offline.json', RESULT_FILE: '/result.json', ...overrides },
  open: () => JSON.stringify(account), http, exec: { scenario: { iterationInTest: 7 } },
  check: () => {}, Counter: metric, Rate: metric, Trend: metric, console: { log: value => messages.push(JSON.parse(value)) } };
  vm.runInNewContext(source, context);
  return { api: context.testApi, cookies, requests, messages, merge: () => { merged = true; } };
}
assert.throws(() => fixture({ BASE_URL: 'https://dev.dameokja.com' }), /운영 대상/);
assert.throws(() => fixture({}, { remainingWriteSlots: undefined }), /잔여 쓰기 용량/);
const f = fixture();
const session = f.api.setup();
delete f.cookies.accessToken;
delete f.cookies['XSRF-TOKEN'];
f.api.write(session);
assert.equal(f.requests.at(-1).params.headers['X-XSRF-TOKEN'], 'offline-csrf');
assert.equal(f.cookies.accessToken[0], 'offline-access');
assert.equal(f.requests.at(-1).body.items[0].name, 'sw-offline1-baseline-7');
assert.equal(f.messages.at(-1).ok, true);
f.merge(); f.api.write(session);
assert.equal(f.messages.at(-1).ok, false);
assert.ok(!JSON.stringify(f.messages).includes('offline-access'));
console.log('k6 오프라인 모의 검증 통과');

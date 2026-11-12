"""요청률과 오류 판정이 경계·누락을 숨기지 않는지 검증한다."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from analyze import analyze


class AnalyzeTest(unittest.TestCase):
    def samples(self, root, minute_rates, error=False, drop=False, mixed=True):
        target=Path(root)/'raw.json'
        start=dt.datetime(2026,11,11,tzinfo=dt.timezone.utc).timestamp()
        with target.open('w') as stream:
            for minute,rate in enumerate(minute_rates):
                for index in range(rate*60):
                    timestamp=start+minute*60+index/rate
                    pattern=['list']*9+['list-next']*9+['write-pick','detail','update','verify']
                    api=pattern[index%22] if mixed else 'list'
                    tags={'phase':'measure','api':api,'status':'200','window_start':str(start*1000)}
                    for metric,value,delay in [('business_sent',1,0),('business_completed',1,.02),('business_latency',20,.02)]:
                        point={'type':'Point','metric':metric,'data':{'time':dt.datetime.fromtimestamp(timestamp+delay,dt.timezone.utc).isoformat(),'value':value,'tags':tags}}
                        stream.write(json.dumps(point)+'\n')
            for metric,value in [('business_error',int(error)),('dropped_iterations',int(drop))]:
                stream.write(json.dumps({'type':'Point','metric':metric,'data':{'time':dt.datetime.fromtimestamp(start,dt.timezone.utc).isoformat(),'value':value,'tags':tags}})+'\n')
        return target

    def test_exact_rate(self):
        with tempfile.TemporaryDirectory() as root:
            result=analyze(self.samples(root,[95]),60)
            self.assertTrue(result['checks']['rate'])
            self.assertTrue(result['checks']['minute_rate'])
            self.assertTrue(result['checks']['api_mix'])
            self.assertTrue(result['preliminary_pass'])
            self.assertEqual(result['sent'],5700)

    def test_average_does_not_hide_minute_drift(self):
        with tempfile.TemporaryDirectory() as root:
            result=analyze(self.samples(root,[93,97]),120)
            self.assertTrue(result['checks']['rate'])
            self.assertFalse(result['checks']['minute_rate'])

    def test_errors_and_drops_fail(self):
        with tempfile.TemporaryDirectory() as root:
            result=analyze(self.samples(root,[95],True,True),60)
            self.assertFalse(result['preliminary_pass'])
            self.assertEqual(result['errors'],1)
            self.assertEqual(result['dropped_iterations'],1)

    def test_missing_write_path_fails(self):
        with tempfile.TemporaryDirectory() as root:
            result=analyze(self.samples(root,[95],mixed=False),60)
            self.assertFalse(result['checks']['api_mix'])
            self.assertFalse(result['preliminary_pass'])


if __name__ == '__main__':
    unittest.main()

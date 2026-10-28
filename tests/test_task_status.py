import json
import threading
from contextlib import closing
from unittest.mock import Mock, patch
import unittest

import test_import_pipeline as pipeline
from connectors import events, media_probe, throttle
from models import import_store
from services import import_jobs, task_progress


class TaskStatusTest(unittest.TestCase):
    setUp = pipeline.ImportJobsTest.setUp
    tearDown = pipeline.ImportJobsTest.tearDown
    connect = pipeline.ImportJobsTest.connect
    seed = pipeline.ImportJobsTest.seed
    selection = pipeline.ImportJobsTest.selection
    submit = pipeline.ImportJobsTest.submit
    finished = pipeline.ImportJobsTest.finished

    def queued(self):
        path = '/share/S01E01.mkv'
        self.seed([path])
        with patch.object(import_jobs, 'start'):
            return self.submit([self.selection([path], 'tt9000002')])[0]

    def task(self, response=None):
        return next(item for item in (response or self.client.get('/manager/tasks')).json['tasks'] if item['kind'] == 'import')

    def test_summary_is_small_conditional_and_does_not_expose_checkpoints(self):
        job = self.queued()
        with closing(import_store._connect(self.database_path)) as db, db:
            db.execute('UPDATE import_job SET probes = ?', (json.dumps({'private': 'x'*100000}),))
        response = self.client.get('/manager/tasks')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.task(response)['state'], 'queued')
        body = response.get_data(as_text=True)
        self.assertNotIn('/share/', body)
        self.assertNotIn('private', body)
        self.assertLess(len(body), 2500)
        self.assertEqual(self.client.get('/manager/tasks', headers={'If-None-Match':response.headers['ETag']}).status_code, 304)
        with closing(import_store._connect(self.database_path)) as db, db:
            db.execute("UPDATE import_job SET state = 'failed', error = ? WHERE job_id = ?", ('bad https://example.test/video?sign=SECRET token=PRIVATE',job['id']))
        changed = self.client.get('/manager/tasks', headers={'If-None-Match':response.headers['ETag']})
        self.assertEqual(changed.status_code, 200)
        self.assertNotIn('SECRET', changed.get_data(as_text=True))
        self.assertNotIn('PRIVATE', changed.get_data(as_text=True))

    def test_live_parallel_episode_names_are_visible_before_completion(self):
        paths = ['/share/S01E01.mkv','/share/S01E02.mkv']
        self.seed(paths)
        release = threading.Event()
        def probe(url):
            assert release.wait(5)
            return {'duration_ms':1000}
        try:
            with patch.object(media_probe,'probe_video',side_effect=probe):
                job = self.submit([self.selection(paths,'tt9000002')])[0]
                task = pipeline.eventually(lambda: (item if len((item:=self.task())['current'])==2 else None))
                self.assertEqual(set(task['current']), {'S01E01','S01E02'})
                self.assertEqual(task['name'], '导入剧集')
                self.assertEqual((task['phase'],task['done'],task['total']),('probe',0,2))
                release.set()
                self.finished([job['id']])
                done = self.task()
                self.assertEqual((done['state'],done['core'],done['files']),('complete',2,2))
        finally:
            release.set()

    def test_busy_wait_and_retry_preserve_probe_checkpoint(self):
        job = self.queued()
        row = import_jobs._claim(self.database_path)
        import_jobs._checkpoint(self.database_path,job['id'],row['owner'],'/share/S01E01.mkv',{'duration_ms':42})
        response = Mock(status_code=503,headers={'Retry-After':'600'})
        with task_progress.Reporter(self.database_path,row) as reporter:
            with self.assertRaises(throttle.RateLimitError):
                with events.activity('probe','1','S01E01'):
                    throttle.call('tmdb',lambda:response)
            reporter.finish()
        import_store.finish(self.database_path,row,'queued',None,'',600)
        data = self.client.get('/manager/tasks').json
        task = next(item for item in data['tasks'] if item['kind']=='import')
        self.assertEqual(task['state'],'waiting')
        self.assertEqual(data['services'][0]['code'],'busy')
        self.assertIn('服务繁忙',json.dumps(data,ensure_ascii=False))
        with closing(import_store._connect(self.database_path)) as db, db:
            db.execute("UPDATE import_job SET state='failed' WHERE job_id=?",(job['id'],))
        with patch.object(import_jobs,'start'):
            retry = self.client.post('/manager/association/jobs',json={'action':'retry','ids':[job['id']]})
        self.assertEqual(retry.status_code,202)
        self.assertEqual(retry.json['jobs'][0]['state'],'queued')
        with closing(import_store._connect(self.database_path)) as db:
            saved = db.execute('SELECT probes,attempts FROM import_job WHERE job_id=?',(job['id'],)).fetchone()
        self.assertEqual(json.loads(saved['probes'])['/share/S01E01.mkv']['duration_ms'],42)
        self.assertEqual(saved['attempts'],0)

    def test_progress_is_coalesced_and_cancellation_fences_observer(self):
        self.queued()
        row = import_jobs._claim(self.database_path)
        with task_progress.Reporter(self.database_path,row) as reporter:
            with patch.object(import_store,'_connect',wraps=import_store._connect) as connect:
                events.emit('plan',stage='image',total=100)
                for i in range(100):
                    with events.activity('image',str(i),'海报'):
                        pass
                reporter.finish()
                self.assertLessEqual(connect.call_count,3)
            import_store.cancel(self.database_path,[row['job_id']])
            reporter.progress('enrich',1,1)
        saved = import_store.status(self.database_path,[row['job_id']])['jobs'][0]
        self.assertEqual(saved['state'],'cancelled')
        self.assertNotIn('core',saved['progress'])
        self.assertEqual(saved['progress']['stages']['image']['done'],100)

    def test_read_only_snapshot_does_not_offer_or_poll_tasks(self):
        from test_library_app import movie_app
        with patch.dict(movie_app.app.config,READ_ONLY=True):
            html = self.client.get('/').get_data(as_text=True)
            self.assertNotIn('data-task-center',html)
            self.assertNotIn('task-center.js',html)
            self.assertEqual(self.client.get('/manager/tasks').status_code,503)

    def test_diagnostics_redact_bearer_and_structured_credentials(self):
        text = events.safe_text('Authorization: Bearer abc.JWT token="secret" api_key=private')
        for value in ('abc.JWT','secret','private'):
            self.assertNotIn(value,text)

    def test_local_queue_timeout_is_not_reported_as_provider_rate_limiting(self):
        gate=throttle.Limiter('tmdb',throttle.Policy(rate=1,burst=1,concurrency=1,max_wait=.1))
        with patch.object(throttle.time,'monotonic',side_effect=[0,1]),patch.object(gate,'blocked_for',return_value=0):
            with self.assertRaises(throttle.RateLimitError) as caught:
                with gate.slot():
                    pass
        self.assertEqual(events.error_info(caught.exception)['code'],'capacity')

    def test_retry_optional_data_after_rescan_reuses_committed_files(self):
        from services import media_discovery
        path='/share/movie.mkv'
        self.seed([path])
        job=self.submit([self.selection([path])])[0]
        self.finished([job['id']])
        with closing(import_store._connect(self.database_path)) as db,db:
            db.execute("UPDATE import_job SET result=? WHERE job_id=?",(json.dumps({'imported':1,'warning':'资料待补齐'}),job['id']))
        media_discovery._scans[str(self.database_path)]['records']=[]
        with patch.object(media_probe,'probe_video') as probe:
            response=self.client.post('/manager/association/jobs',json={'action':'retry','ids':[job['id']]})
            self.assertEqual(response.status_code,202)
            self.assertEqual(self.finished([job['id']])[0]['state'],'complete')
            probe.assert_not_called()

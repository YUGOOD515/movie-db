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
        self.assertIn('poster', self.task(response))  # 悬浮岛的海报缩略图；作品尚未入库时为 None
        self.assertEqual(self.task(response)['linked'], 0)  # 关联四步里“获取链接”的进度
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

    def test_detail_lists_files_by_episode_and_is_conditional(self):
        self.queued()
        task_id = self.task()['id']
        response = self.client.get('/manager/tasks/detail', query_string={'id': task_id})
        self.assertEqual(response.status_code, 200)
        detail = response.json
        self.assertEqual([(item['position'], item['season'], item['state']) for item in detail['files']], [('S01E01', 1, 'pending')])
        self.assertTrue(detail['series'])
        self.assertEqual(detail['outcome'], '')
        self.assertNotIn('/share/', response.get_data(as_text=True))  # 只给文件名，不给网盘路径
        self.assertEqual(self.client.get('/manager/tasks/detail', query_string={'id': task_id},
                                         headers={'If-None-Match': response.headers['ETag']}).status_code, 304)
        self.assertEqual(self.client.get('/manager/tasks/detail', query_string={'id': 'import:tt0:missing'}).status_code, 404)

    def test_imdb_index_update_progress_and_failure_are_visible(self):
        state = {"started_at": 1, "running": True, "phase": "download", "downloaded": 1048576}
        with patch('services.imdb_identity.status', return_value=state):
            response = self.client.get('/manager/tasks')
            item = next(task for task in response.json['tasks'] if task['kind'] == 'imdb')
            self.assertEqual((item['state'], item['phase'], item['total']), ('running', 'imdb-download', 0))
            self.assertIn('1.0 MiB', item['detail'])
            state.update(phase='build', processed_rows=50000)
            changed = self.client.get('/manager/tasks', headers={'If-None-Match': response.headers['ETag']})
            self.assertEqual(changed.status_code, 200)
            item = next(task for task in changed.json['tasks'] if task['kind'] == 'imdb')
            self.assertEqual(item['phase'], 'imdb-build')
            self.assertIn('50,000', item['detail'])
            state.update(running=False, phase='failed', error='网络失败，旧库保留', finished_at=2)
            item = next(task for task in self.client.get('/manager/tasks').json['tasks'] if task['kind'] == 'imdb')
            self.assertEqual((item['state'], item['phase']), ('failed', 'failed'))
            self.assertTrue(item['issues'])

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

    def test_completed_job_settles_older_failed_job_whose_files_are_now_linked(self):
        job = self.queued()
        with closing(import_store._connect(self.database_path)) as db, db:
            db.execute("UPDATE import_job SET state='failed', error='旧的失败' WHERE job_id=?", (job['id'],))
        # 文件还没关联好：旧任务保持失败
        with patch.object(import_store, '_still_associated', return_value=False):
            self.assertEqual(import_store.settle_superseded(self.database_path, 'tt9000002'), [])
        self.assertEqual(self.task()['state'], 'failed')
        # 别的任务完成、这些文件已按原映射关联：旧任务一并标成完成，不再留在任务中心
        with patch.object(import_store, 'settle_superseded', wraps=import_store.settle_superseded) as settle,              patch.object(import_store, '_still_associated', return_value=True):
            import_store.finish(self.database_path, {'job_id': 'other', 'owner': 'x', 'title_id': 'tt9000002'}, 'complete', None, '', 0)
        settle.assert_called_once_with(self.database_path, 'tt9000002')
        self.assertEqual(import_store.status(self.database_path, [job['id']])['jobs'][0]['state'], 'complete')
        self.assertEqual(import_store.status(self.database_path, [job['id']])['jobs'][0]['error'], '')

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

    def test_retry_one_failed_file_keeps_success_and_other_failure(self):
        paths = ['/share/a.mkv', '/share/b.mkv', '/share/c.mkv']
        self.seed(paths)
        def probe(url):
            if '/c.mkv' not in url:
                raise media_probe.ProbeError('文件无法识别为视频，请检查文件。')
            return {'duration_ms': 42}
        with patch.object(media_probe, 'probe_video', side_effect=probe):
            job = self.submit([self.selection(paths)])[0]
            self.finished([job['id']])
        import_jobs.stop(self.database_path)
        task = self.task()
        self.assertEqual((task['state'], task['core'], task['failed']), ('partial', 1, 2))
        self.assertTrue(all(issue.get('label') for issue in task['issues']))
        detail = self.client.get('/manager/tasks/detail', query_string={'id': task['id']}).json
        self.assertEqual(detail['outcome'], '已关联 1 个视频，失败 2 个视频')
        self.assertEqual([file['written'] for file in detail['files']], [False, False, True])
        self.assertEqual(detail['files'][0]['job_id'], job['id'])
        for indexes in ([3], [999], [True], []):
            response = self.client.post('/manager/association/jobs', json={'action': 'retry', 'ids': [job['id']], 'indexes': indexes})
            self.assertEqual(response.status_code, 400)
        with patch.object(import_jobs, 'start'):
            response = self.client.post('/manager/association/jobs', json={'action': 'retry', 'ids': [job['id']], 'indexes': [1]})
        self.assertEqual(response.status_code, 202)
        # An unrelated failed file disappearing must not block this file's retry.
        from services import media_discovery
        media_discovery._scans[str(self.database_path)]['records'][0]['files'] = [
            item for item in media_discovery._scans[str(self.database_path)]['records'][0]['files'] if item['path'] != paths[1]]
        with patch.object(media_probe, 'probe_video', return_value={'duration_ms': 43}) as probe:
            import_jobs._execute(self.database_path, import_jobs._claim(self.database_path))
        self.assertEqual(probe.call_count, 1)
        self.assertIn('/a.mkv', probe.call_args.args[0])
        result = self.finished([job['id']])[0]
        self.assertEqual([item['written'] for item in result['result']['rows']], [True, False, True])
        self.assertTrue(result['result']['rows'][1]['error'])
        detail = self.client.get('/manager/tasks/detail', query_string={'id': task['id']}).json
        self.assertEqual(detail['outcome'], '已关联 2 个视频，失败 1 个视频')

    def test_5xx_backoff_keeps_success_and_automatically_retries_only_failure(self):
        import time
        paths = ['/share/S01E01.mkv', '/share/S01E02.mkv']
        self.seed(paths)
        with patch.object(import_jobs, 'start'):
            job = self.submit([self.selection(paths, 'tt9000002')])[0]
        def probe(url):
            if 'S01E01' in url:
                message, code = media_probe.explain_failure('Server returned 5XX Server Error reply')
                raise media_probe.ProbeError(message, code=code, retryable=True)
            return {'duration_ms': 42}
        with patch.object(media_probe, 'probe_video', side_effect=probe):
            import_jobs._execute(self.database_path, import_jobs._claim(self.database_path))
        waiting = import_store.status(self.database_path, [job['id']])['jobs'][0]
        self.assertEqual(waiting['state'], 'queued')
        self.assertGreater(waiting['next_at'], time.time())
        task = self.task()
        self.assertEqual((task['state'], task['core']), ('waiting', 1))
        self.assertTrue(all(issue.get('label') for issue in task['issues']))
        with closing(import_store._connect(self.database_path)) as db, db:
            db.execute('UPDATE import_job SET next_at=0 WHERE job_id=?', (job['id'],))
        with patch.object(media_probe, 'probe_video', return_value={'duration_ms': 43}) as probe:
            import_jobs._execute(self.database_path, import_jobs._claim(self.database_path))
        self.assertEqual(probe.call_count, 1)
        self.assertIn('S01E01', probe.call_args.args[0])
        self.assertEqual(self.finished([job['id']])[0]['state'], 'complete')
        detail = self.client.get('/manager/tasks/detail', query_string={'id': task['id']}).json
        self.assertEqual([file['written'] for file in detail['files']], [True, True])
        self.assertEqual(detail['issues'], [])

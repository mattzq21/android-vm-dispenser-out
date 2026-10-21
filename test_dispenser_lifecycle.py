import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch


class Timer:
 def __init__(self, seconds, callback):
  self.seconds=seconds; self.callback=callback; self.cancelled=False
 def start(self): pass
 def cancel(self): self.cancelled=True


class LifecycleTests(unittest.TestCase):
 def setUp(self):
  source=next(Path(__file__).parent.glob('*-vm-dispenser'))
  loader=importlib.machinery.SourceFileLoader('dispenser_under_test',str(source))
  spec=importlib.util.spec_from_loader(loader.name,loader)
  self.m=importlib.util.module_from_spec(spec); loader.exec_module(self.m)
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name); (root/'instances').mkdir()
  self.now=1000
  for target, value in [('STATE',root),('alive',lambda r:r.get('live',False))]:
   patcher=patch.object(self.m,target,value); patcher.start(); self.addCleanup(patcher.stop)
  for target, value in [('Timer',Timer)]:
   patcher=patch.object(self.m.threading,target,value); patcher.start(); self.addCleanup(patcher.stop)
  patcher=patch.object(self.m.time,'monotonic',lambda:self.now); patcher.start(); self.addCleanup(patcher.stop)
  b=self.b=self.m.Dispenser.__new__(self.m.Dispenser)
  b.c={'min_free_gb':0,'capacity':1}; b.runtime=root; b.lock=threading.RLock()
  b.records={}; b.leases={}; b.activity={}; b.busy=set(); b.stopping=False
  b.idle_timer=None; b.disconnected={}
  b.stop=lambda r:r.update(live=False)
  self.token='a'*64; self.key=hashlib.sha256(self.token.encode()).hexdigest()[:32]
  folder=root/'instances'/self.key; folder.mkdir(); disk=folder/'disk'; disk.touch()
  self.record={'id':self.key,'disk':str(disk),'qmp':str(root/'qmp'),'live':True,'slot':0}
  b.records[self.key]=self.record; b.leases[self.key]=object(); b.touch(self.key)
 def test_disconnect_then_delete(self):
  self.b.disconnect(self.key)
  self.assertTrue(self.record['live']); self.assertEqual(self.b.idle_timer.seconds,300)
  timer=self.b.disconnected[self.key][1]; self.assertEqual(timer.seconds,15)
  self.now+=15; timer.callback()
  self.assertNotIn(self.key,self.b.records)
  self.assertFalse(Path(self.record['disk']).parent.exists())
 def test_owner_reconnect_cancels_old_deletion(self):
  self.b.disconnect(self.key); timer=self.b.disconnected[self.key][1]
  self.now+=14; conn=object(); key,record=self.b.acquire(self.token,conn)
  self.assertIs(record,self.record); self.assertEqual(key,self.key)
  self.assertTrue(timer.cancelled); self.assertIsNone(self.b.idle_timer)
  timer.callback(); self.assertTrue(record['live'])
  self.b.disconnect(key); new=self.b.disconnected[key][1]
  timer.callback(); self.assertIn(key,self.b.records)
  self.now+=15; new.callback(); self.assertNotIn(key,self.b.records)
 def test_late_reconnect_gets_fresh_device(self):
  self.b.disconnect(self.key); old_timer=self.b.disconnected[self.key][1]
  self.now+=15
  base=self.b.runtime/'base'; base.mkdir(); (base/'config.ini').touch()
  self.b.c['base']=str(base)
  self.b.start=lambda r:r.update(live=True,slot=0)
  with patch.object(self.m.subprocess,'run'):
   key,record=self.b.acquire(self.token,object())
  self.assertEqual(key,self.key); self.assertIsNot(record,self.record)
  old_timer.callback(); self.assertIs(self.b.records[key],record)
 def test_other_owner_cannot_claim_device(self):
  self.b.disconnect(self.key)
  with self.assertRaisesRegex(RuntimeError,'capacity'): self.b.acquire('b'*64,object())
  with self.assertRaises(ValueError): self.b.acquire(self.key,object())
  self.assertIn(self.key,self.b.disconnected)
 def test_duplicate_connection_rejected(self):
  with self.assertRaisesRegex(RuntimeError,'already leased'): self.b.acquire(self.token,object())
 def test_disconnect_gets_full_grace_near_idle_limit(self):
  self.now+=299; self.b.disconnect(self.key)
  self.now+=14; self.b.collect(); self.assertIn(self.key,self.b.records)
  self.now+=1; self.b.collect(); self.assertNotIn(self.key,self.b.records)
 def test_connected_idle_limit_unchanged(self):
  self.b.leases[self.key]=socket.socket(); self.addCleanup(self.b.leases[self.key].close)
  self.now+=300; self.b.collect(); self.assertNotIn(self.key,self.b.records)
 def test_busy_request_does_not_expire(self):
  self.b.touch(self.key,busy=True); self.now+=400; self.b.collect()
  self.assertIn(self.key,self.b.records)
 def test_socket_eof_schedules_deletion(self):
  server,client=socket.socketpair(); self.addCleanup(server.close); self.addCleanup(client.close)
  self.b.leases.clear()
  self.b.ready=lambda r,c:None
  self.b.c.update(port_start=5570,auto_view=False)
  self.m.send(client,{'op':'acquire','instance':self.token})
  thread=threading.Thread(target=self.b.handle,args=(server,)); thread.start()
  self.assertTrue(self.m.receive(client)['ok']); client.close(); thread.join(2)
  self.assertFalse(thread.is_alive()); self.assertIn(self.key,self.b.disconnected)
  self.assertTrue(self.record['live'])


if __name__=='__main__': unittest.main()

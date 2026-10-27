"""Host configuration recovery and command-speed contracts; no VM is launched."""
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import discover_config, ensure_config, host_defaults, save_config, runtime_candidates
from vm import command_line
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'go2_jazzy'))
import teleop_settings


def host(cpu='Intel(R) Core(TM) i9-14900HX', memory=32768):
    return {'host_cpu':cpu, 'windows_build':26300, 'host_memory_mib':memory,
            'host_logical_cpus':32, 'ssh':'C:/Windows/System32/OpenSSH/ssh.exe'}


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='RobotDogPortability-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, label):
        root = self.root/label
        for relative in ['jazzy/robot-dog-clean.qcow2','jazzy/robot-dog-seed.iso',
                         'qemu/qemu-go2-jazzy.exe','robot_vm_key']:
            path = root/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'test fixture; never executed')
        return root

    def test_desktop_workaround_is_not_applied_to_laptop(self):
        desktop = host_defaults(host('Intel(R) Core(TM) i7-14700K',65536))
        laptop = host_defaults(host())
        self.assertEqual(desktop['whpx_kernel_irqchip'],'off')
        self.assertNotIn('whpx_kernel_irqchip',laptop)
        self.assertEqual(desktop['cpus'],8)
        self.assertEqual(laptop['memory_mib'],12288)

    def test_legacy_laptop_runtime_is_adopted_with_its_name_and_ports(self):
        root = self.runtime('笔记本 已安装环境')
        (root/'jazzy/vm-command.json').write_text(json.dumps([
            'qemu.exe','-name','Go2 ROS2 Jazzy CHAMP','-netdev',
            'user,id=net0,hostfwd=tcp:127.0.0.1:2281-:22','-qmp',
            'tcp:127.0.0.1:2282,server=on,wait=off']))
        config = discover_config(root,host())
        self.assertEqual(config['vm_name'],'Go2 ROS2 Jazzy CHAMP')
        self.assertEqual((config['ssh_port'],config['qmp_port']),(2281,2282))
        args = command_line(config)
        self.assertEqual(args[args.index('-accel')+1],'whpx')
        self.assertEqual(args[args.index('-name')+1],config['vm_name'])
        self.assertIn(str(root),args[args.index('-drive')+1])

    def test_new_clone_recovers_same_host_settings_and_identity(self):
        root = self.runtime('台式机 环境')
        config = discover_config(root,host('Intel(R) Core(TM) i7-14700K',65536))
        config.update(cpus=6,display='gtk')
        save_config(self.root/'first-clone.json',config)
        second = self.root/'new-clone.json'
        with patch('common.runtime_candidates',return_value=[root]), patch('common.preflight',return_value=host()):
            recovered = ensure_config(second)
        self.assertEqual(recovered['instance_id'],config['instance_id'])
        self.assertEqual(recovered['cpus'],6)
        self.assertEqual(recovered['display'],'gtk')
        self.assertEqual(recovered['whpx_kernel_irqchip'],'off')
        self.assertTrue(second.exists())

    def test_copied_profile_recomputes_hardware_settings(self):
        root = self.runtime('copied-runtime')
        config = discover_config(root,host('Intel(R) Core(TM) i7-14700K',65536))
        config['host_id']='a-different-computer'
        save_config(self.root/'original.json',config)
        recovered = discover_config(root,host())
        self.assertNotIn('whpx_kernel_irqchip',recovered)
        self.assertEqual(recovered['host_id'],platform.node())
        self.assertEqual(recovered['host_cpu'],host()['host_cpu'])
        self.assertEqual(recovered['hardware_profile'],'default')
        with patch('common.preflight',return_value=host()):
            recovered_local = ensure_config(self.root/'original.json')
        self.assertNotIn('whpx_kernel_irqchip',recovered_local)
        self.assertEqual(recovered_local['host_cpu'],host()['host_cpu'])

    def test_explicit_runtime_environment_selects_one_instance(self):
        root = self.runtime('explicit')
        with patch.dict('os.environ',{'ROBOTDOG_RUNTIME':str(root)}):
            self.assertEqual(runtime_candidates(),[root.resolve()])

    def test_ambiguous_instances_and_wrong_owner_are_preserved(self):
        a,b=self.runtime('a'),self.runtime('b')
        with patch('common.runtime_candidates',return_value=[a,b]):
            with self.assertRaises(RuntimeError):discover_config(report=host())
        config=discover_config(a,host())
        save_config(self.root/'a.json',config)
        before=(a/'robotdog-host.json').read_bytes()
        with self.assertRaises(RuntimeError):save_config(self.root/'b.json',{**config,'instance_id':'wrong'})
        self.assertEqual((a/'robotdog-host.json').read_bytes(),before)

    def test_speeds_persist_and_reach_all_direction_commands(self):
        path=self.root/'teleop.json'
        values={'forward':.21,'reverse':.09,'turn':.37}
        teleop_settings.save(values,path)
        loaded=teleop_settings.load(path)
        self.assertEqual(teleop_settings.command('forward',loaded),(.21,0))
        self.assertEqual(teleop_settings.command('reverse',loaded),(-.09,0))
        self.assertEqual(teleop_settings.command('left',loaded),(0,.37))
        self.assertEqual(teleop_settings.command('right',loaded),(0,-.37))

    def test_invalid_speeds_do_not_replace_saved_settings(self):
        path=self.root/'teleop.json'
        teleop_settings.save(teleop_settings.DEFAULTS,path)
        before=path.read_bytes()
        for value in [float('nan'),float('inf'),True,-.1,0,.31,'invalid']:
            with self.subTest(value=value), self.assertRaises((ValueError,TypeError)):
                teleop_settings.save({**teleop_settings.DEFAULTS,'forward':value},path)
            self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':
    unittest.main()

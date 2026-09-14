import os
import traceback

from packaging.version import Version


class H200SkuSpec:
    def _h200_check_fw(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line_idx, line in enumerate(dmidecode_lines):
            if 'BIOS Revision: ' in line:
                ver = line.split('BIOS Revision: ')[1]
                assert Version(ver) >= Version('5.32'), \
                    f"BIOS Revision {ver} < 5.32"
            elif 'BMC FW Version' in line:
                ver = dmidecode_lines[line_idx + 1]
                assert Version(ver) >= Version('1.03.0000'), \
                    f"BMC FW Version {ver} < 1.03.0000"

    def _h200_check_cpu_48c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'INTEL(R) XEON(R) PLATINUM 8558', \
            f"CPU: '{cpu['Model name']}', expected 'INTEL(R) XEON(R) PLATINUM 8558'"
        assert cpu['Core(s) per socket'] == '48', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 48"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '4000.0000', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 4000.0000"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _h200_check_mem_128g16(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '2T', \
            f"Memory total: {mem['total_capacity']}, expected 2T"
        num_memory_devices = 16
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 5600 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit='{dev['Speed'].split()[1]}', expected MT/s"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 4800, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 4800 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('128 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 128 GB"

    def _h200_check_mem_64g32(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '2T', \
            f"Memory total: {mem['total_capacity']}, expected 2T"
        num_memory_devices = 32
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 5600 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 4400, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 4400 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('64 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 64 GB"

    def _h200_check_gpu_h200x8(self, sys_info):
        accel = sys_info['Accelerator']
        assert accel['gpu_count'] == '8', \
            f"GPU count: {accel['gpu_count']}, expected 8"
        for i, gpu in enumerate(accel['nvidia_info']['gpu']):
            assert gpu['product_name'] == 'NVIDIA H200', \
                f"GPU {i}: product_name='{gpu['product_name']}', expected 'NVIDIA H200'"
            assert gpu['fb_memory_usage']['total'] == '143771 MiB', \
                f"GPU {i}: fb_memory total='{gpu['fb_memory_usage']['total']}', expected 143771 MiB"
            assert gpu['max_clocks']['graphics_clock'] == '1980 MHz', \
                f"GPU {i}: graphics_clock='{gpu['max_clocks']['graphics_clock']}', expected 1980 MHz"
            assert gpu['max_clocks']['sm_clock'] == '1980 MHz', \
                f"GPU {i}: sm_clock='{gpu['max_clocks']['sm_clock']}', expected 1980 MHz"
            assert gpu['max_clocks']['mem_clock'] == '3201 MHz', \
                f"GPU {i}: mem_clock='{gpu['max_clocks']['mem_clock']}', expected 3201 MHz"
            assert gpu['max_clocks']['video_clock'] == '1545 MHz', \
                f"GPU {i}: video_clock='{gpu['max_clocks']['video_clock']}', expected 1545 MHz"
            assert gpu['gpu_power_readings']['max_power_limit'] == '700.00 W', \
                f"GPU {i}: max_power_limit='{gpu['gpu_power_readings']['max_power_limit']}', expected 700.00 W"
            assert gpu['vbios_version'] >= '96.00.A5.00.03', \
                f"GPU {i}: vbios_version='{gpu['vbios_version']}' < 96.00.A5.00.03"
        for nvswitch_bda in accel['nvswitch_info']:
            for entry in accel['nvswitch_info'][nvswitch_bda]:
                if 'BIOS Version: ' in entry:
                    ver = entry.split('BIOS Version: ')[1]
                    assert ver >= '96.10.57.00.01', \
                        f"NVSwitch {nvswitch_bda}: BIOS Version='{ver}' < 96.10.57.00.01"

    def _h200_check_nic_ib400g8_rc400g2(self, sys_info):
        num_ib_devices = 10
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        for ib_device_idx in range(num_ib_devices):
            nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
            assert 'Mellanox Technologies MT2910 Family [ConnectX-7]' == nic_model, \
                f"mlx5_{ib_device_idx}: expected ConnectX-7, got '{nic_model}'"
            ib_device_status_info_key = f'CA \'mlx5_{ib_device_idx}\''
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            fw_ver = ib_device_status_info['Firmware version']
            assert Version(fw_ver) >= Version('28.39.3560'), \
                f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.39.3560"
            port_info = ib_device_status_info['Port 1:']
            assert port_info['Rate'] == '400', \
                f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 400"
            assert port_info['State'] == 'Active', \
                f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
            assert port_info['Physical state'] == 'LinkUp', \
                f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            link_layer = port_info['Link layer']
            if ib_device_idx in [3, 8]:
                assert link_layer == 'Ethernet', \
                    f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected 'Ethernet'"
            else:
                assert link_layer == 'InfiniBand', \
                    f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected 'InfiniBand'"

    def _h200_get_disks_to_check_3t2_15t16(self):
        disks_to_check = {}
        num_os_disks = 2
        num_data_disks = 16
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZ1L23T8HBLA-00A07', 'Micron_7450_MTFDKBG3T8TFR'],
                'Firmware': ['GDC7202Q', 'E2MU200'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 15360950534144,
                'ModelNumber': ['SOLIDIGM SB5PH27X153T', 'SOLIDIGM SB5PH27X153TOP'],
                'Firmware': ['G70YG100', 'G70YG100'],
            }
        return disks_to_check

    def _h200_get_disks_to_check_3t2_15t1(self):
        disks_to_check = {}
        num_os_disks = 2
        num_data_disks = 1
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZ1L23T8HBLA-00A07', 'Micron_7450_MTFDKBG3T8TFR'],
                'Firmware': ['GDC7302Q', 'E2MU200'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 15360950534144,
                'ModelNumber': ['SOLIDIGM SB5PH27X153T'],
                'Firmware': ['G70YG100'],
            }
        return disks_to_check

    def _h200_get_disks_to_check_960g1_3t4(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 4
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZQL2960HCJR-00A07', 'SAMSUNG MZQL2960HCJR-00B7C', 'SAMSUNG MZQL2960HCJR-00BAL', 'INTEL SSDPF2KX960HZ'],
                'Firmware': ['GDC5602Q', 'GDC5A02Q', 'GDC51Z2Q', 'YCV10200'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZQL23T8HCLS-00A07', 'SAMSUNG MZQL23T8HCLS-00B7C', 'SOLIDIGM SSDPF2KX038T1'],
                'Firmware': ['GDC5A02Q', 'GDC54C2Q', '9CV10490'],
            }
        return disks_to_check

    def _h200_check_disks(self, sys_info, get_disks_to_check):
        disks_to_check = get_disks_to_check()
        checkd_disks = 0
        for disk_info in sys_info['Storage']['block_device']:
            disk_name = disk_info['NAME']
            if disk_name in disks_to_check:
                expected = disks_to_check[disk_name]
                assert disk_info['ModelNumber'] in expected['ModelNumber'], \
                    f"{disk_name}: ModelNumber='{disk_info['ModelNumber']}', expected one of {expected['ModelNumber']}"
                model_idx = expected['ModelNumber'].index(disk_info['ModelNumber'])
                assert disk_info['Firmware'] >= expected['Firmware'][model_idx], \
                    f"{disk_name}: Firmware='{disk_info['Firmware']}' < '{expected['Firmware'][model_idx]}'"
                assert disk_info['PhysicalSize'] == expected['PhysicalSize'], \
                    f"{disk_name}: PhysicalSize={disk_info['PhysicalSize']}, expected {expected['PhysicalSize']}"
                checkd_disks += 1
            else:
                assert not (disk_name.startswith('nvme') and 'MODEL' in disk_info), \
                    f"Unexpected NVMe disk: {disk_name}"
                # Skip BMC virtual media devices (sd* with 0 size or "Virtual" in MODEL)
                if disk_name.startswith('sd') and 'MODEL' in disk_info:
                    model = disk_info.get('MODEL', '')
                    size = disk_info.get('SIZE', '0')
                    if 'Virtual' in model or size in ('0B', '0'):
                        continue
                    assert False, f"unexpected sd disk: {disk_name} MODEL={model} SIZE={size}"
        assert checkd_disks == len(disks_to_check), \
            f"Checked {checkd_disks} disks, expected {len(disks_to_check)}"

    def _h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_3t2_15t16(self, sys_info):
        self._h200_check_fw(sys_info)
        self._h200_check_cpu_48c2(sys_info)
        self._h200_check_mem_128g16(sys_info)
        self._h200_check_gpu_h200x8(sys_info)
        self._h200_check_nic_ib400g8_rc400g2(sys_info)
        self._h200_check_disks(sys_info, self._h200_get_disks_to_check_3t2_15t16)

    def _h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_3t2_15t1(self, sys_info):
        self._h200_check_fw(sys_info)
        self._h200_check_cpu_48c2(sys_info)
        self._h200_check_mem_128g16(sys_info)
        self._h200_check_gpu_h200x8(sys_info)
        self._h200_check_nic_ib400g8_rc400g2(sys_info)
        self._h200_check_disks(sys_info, self._h200_get_disks_to_check_3t2_15t1)

    def _h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_960g1_3t4(self, sys_info):
        self._h200_check_fw(sys_info)
        self._h200_check_cpu_48c2(sys_info)
        self._h200_check_mem_128g16(sys_info)
        self._h200_check_gpu_h200x8(sys_info)
        self._h200_check_nic_ib400g8_rc400g2(sys_info)
        self._h200_check_disks(sys_info, self._h200_get_disks_to_check_960g1_3t4)

    def _h200_check_sku_48c2_64g32_h200x8_ib400g8_rc400g2_960g1_3t4(self, sys_info):
        self._h200_check_fw(sys_info)
        self._h200_check_cpu_48c2(sys_info)
        self._h200_check_mem_64g32(sys_info)
        self._h200_check_gpu_h200x8(sys_info)
        self._h200_check_nic_ib400g8_rc400g2(sys_info)
        self._h200_check_disks(sys_info, self._h200_get_disks_to_check_960g1_3t4)

    def _sku_to_checker(self, sku_str):
        if sku_str == 'h200-48c2-128g16-h200x8-ib400g8-rc400g2-3t2-15t16':
            return self._h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_3t2_15t16
        elif sku_str == 'h200-48c2-128g16-h200x8-ib400g8-rc400g2-3t2-15t1':
            return self._h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_3t2_15t1
        elif sku_str == 'h200-48c2-128g16-h200x8-ib400g8-rc400g2-960g1-3t4':
            return self._h200_check_sku_48c2_128g16_h200x8_ib400g8_rc400g2_960g1_3t4
        elif sku_str == 'h200-48c2-64g32-h200x8-ib400g8-rc400g2-960g1-3t4':
            return self._h200_check_sku_48c2_64g32_h200x8_ib400g8_rc400g2_960g1_3t4
        return None

    def _fuzz_match_with_mem_disk(self, sys_info):
        min_distance = 3
        min_sku = None
        mem_disk_checkers = [
            [self._h200_check_mem_128g16, self._h200_get_disks_to_check_3t2_15t16],
            [self._h200_check_mem_128g16, self._h200_get_disks_to_check_3t2_15t1],
            [self._h200_check_mem_64g32, self._h200_get_disks_to_check_960g1_3t4],
        ]
        sku_list = self.sku_list()
        for sku_idx, sku in enumerate(sku_list):
            sku_distance = 0
            try:
                mem_disk_checkers[sku_idx][0](sys_info)
            except:
                sku_distance += 1
            try:
                self._h200_check_disks(sys_info, mem_disk_checkers[sku_idx][1])
            except:
                sku_distance += 1
            if sku_distance < min_distance:
                min_distance = sku_distance
                min_sku = sku
        return min_sku

    def sku_list(self):
        return [
            'h200-48c2-128g16-h200x8-ib400g8-rc400g2-3t2-15t16',
            'h200-48c2-128g16-h200x8-ib400g8-rc400g2-3t2-15t1',
            'h200-48c2-128g16-h200x8-ib400g8-rc400g2-960g1-3t4',
            'h200-48c2-64g32-h200x8-ib400g8-rc400g2-960g1-3t4',
        ]

    def check(self, sys_info, sku_str):
        checker = self._sku_to_checker(sku_str)
        if not checker:
            return False, f'unknown sku {sku_str}'
        try:
            checker(sys_info)
            return True, ''
        except:
            return False, traceback.format_exc()

    def match(self, sys_info):
        match_fail_info = 'match failed' + os.linesep
        for sku_str in self.sku_list():
            success, check_fail_info = self.check(sys_info, sku_str)
            if success:
                return True, sku_str
            else:
                match_fail_info += f'case {sku_str}:' + os.linesep
                match_fail_info += check_fail_info + os.linesep
        fuzz_match_sku = self._fuzz_match_with_mem_disk(sys_info)
        match_fail_info += f'fuzz match: {fuzz_match_sku}'
        return False, match_fail_info


class CtrlSkuSpec:
    def _ctrl_check_fw(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line_idx, line in enumerate(dmidecode_lines):
            if 'BIOS Revision: ' in line:
                ver = line.split('BIOS Revision: ')[1]
                assert Version(ver) >= Version('5.27'), \
                    f"BIOS Revision {ver} < 5.27"
            elif 'Firmware Component Name: BMC Firmware' in line:
                next_line = dmidecode_lines[line_idx + 1]
                assert 'Firmware Version' in next_line, \
                    f"BMC FW: expected 'Firmware Version' in '{next_line}'"
                ver = next_line.split()[-1]
                assert Version(ver) >= Version('1.85'), \
                    f"BMC FW Version {ver} < 1.85"

    def _ctrl_check_cpu_32c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'AMD EPYC 9354 32-Core Processor', \
            f"CPU: '{cpu['Model name']}', expected 'AMD EPYC 9354 32-Core Processor'"
        assert cpu['Core(s) per socket'] == '32', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 32"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '3799.0720', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 3799.0720"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _ctrl_check_mem_64g12(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '770G', \
            f"Memory total: {mem['total_capacity']}, expected 770G"
        num_memory_devices = 12
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 5600 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 4800, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 4800 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('64 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 64 GB"

    def _ctrl_check_nic_rc100g1_dev1(self, sys_info):
        num_ib_devices = 1
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        nic_model = sys_info['Network']['nic'][0]['model']
        assert 'Mellanox Technologies MT2892 Family [ConnectX-6 Dx]' == nic_model, \
            f"mlx5_0: expected ConnectX-6 Dx, got '{nic_model}'"
        ib_device_status_info = sys_info['Network']['ib']['ib_device_status']["CA 'mlx5_0'"]
        fw_ver = ib_device_status_info['Firmware version']
        assert Version(fw_ver) >= Version('22.37.1014'), \
            f"mlx5_0: firmware {fw_ver} < 22.37.1014"
        port_info = ib_device_status_info['Port 1:']
        assert port_info['Rate'] == '100', \
            f"mlx5_0: Rate={port_info['Rate']}, expected 100"
        assert port_info['State'] == 'Active', \
            f"mlx5_0: State={port_info['State']}, expected Active"
        assert port_info['Physical state'] == 'LinkUp', \
            f"mlx5_0: Physical state={port_info['Physical state']}, expected LinkUp"
        assert port_info['Link layer'] == 'Ethernet', \
            f"mlx5_0: Link layer='{port_info['Link layer']}', expected Ethernet"

    def _ctrl_check_nic_rc100g1_dev2(self, sys_info):
        num_ib_devices = 2
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        for ib_device_idx in range(num_ib_devices):
            nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
            assert 'Mellanox Technologies MT2892 Family [ConnectX-6 Dx]' == nic_model, \
                f"mlx5_{ib_device_idx}: expected ConnectX-6 Dx, got '{nic_model}'"
            ib_device_status_info_key = f"CA 'mlx5_{ib_device_idx}'"
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            fw_ver = ib_device_status_info['Firmware version']
            assert Version(fw_ver) >= Version('22.43.2566'), \
                f"mlx5_{ib_device_idx}: firmware {fw_ver} < 22.43.2566"
            port_info = ib_device_status_info['Port 1:']
            if ib_device_idx == 0:
                assert port_info['Rate'] == '100', \
                    f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 100"
                assert port_info['State'] == 'Active', \
                    f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
                assert port_info['Physical state'] == 'LinkUp', \
                    f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            else:
                assert port_info['Rate'] == '40', \
                    f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 40"
                assert port_info['State'] == 'Down', \
                    f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Down"
                assert port_info['Physical state'] == 'Disabled', \
                    f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected Disabled"
            assert port_info['Link layer'] == 'Ethernet', \
                f"mlx5_{ib_device_idx}: Link layer='{port_info['Link layer']}', expected Ethernet"

    def _ctrl_get_disks_to_check_960g1_7t2(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 2
        for disk_idx in range(num_os_disks):
            disks_to_check['sda'] = {
                'SIZE': '894.2G',
                'Rotational': '0',
            }
        for disk_idx in range(num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 7681501126656,
                'ModelNumber': ['SOLIDIGM SB5PH27X076T'],
                'Firmware': ['G70YG100'],
            }
        return disks_to_check

    def _ctrl_get_disks_to_check_960g2_7t2(self):
        disks_to_check = {}
        num_os_disks = 2
        num_data_disks = 2
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZQL2960HCJR-00B7C'],
                'Firmware': ['GDC59C2Q'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 7681501126656,
                'ModelNumber': ['SAMSUNG MZQL27T6HBLA-00B7C'],
                'Firmware': ['GDC59C2Q'],
            }
        return disks_to_check

    def _ctrl_get_disks_to_check_960g2_7t8(self):
        disks_to_check = {}
        num_os_disks = 2
        num_data_disks = 8
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZQL2960HCJR-00B7C'],
                'Firmware': ['GDC59C2Q'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 7681501126656,
                'ModelNumber': ['SAMSUNG MZQL27T6HBLA-00B7C'],
                'Firmware': ['GDC59C2Q'],
            }
        return disks_to_check

    def _ctrl_check_disks(self, sys_info, get_disks_to_check):
        disks_to_check = get_disks_to_check()
        checkd_disks = 0
        for disk_info in sys_info['Storage']['block_device']:
            disk_name = disk_info['NAME']
            if disk_name in disks_to_check:
                expected = disks_to_check[disk_name]
                if disk_name.startswith('nvme'):
                    assert disk_info['ModelNumber'] in expected['ModelNumber'], \
                        f"{disk_name}: ModelNumber='{disk_info['ModelNumber']}', expected one of {expected['ModelNumber']}"
                    model_idx = expected['ModelNumber'].index(disk_info['ModelNumber'])
                    assert disk_info['Firmware'] >= expected['Firmware'][model_idx], \
                        f"{disk_name}: Firmware='{disk_info['Firmware']}' < '{expected['Firmware'][model_idx]}'"
                    assert disk_info['PhysicalSize'] == expected['PhysicalSize'], \
                        f"{disk_name}: PhysicalSize={disk_info['PhysicalSize']}, expected {expected['PhysicalSize']}"
                else:
                    assert disk_info['SIZE'] == expected['SIZE'], \
                        f"{disk_name}: SIZE='{disk_info['SIZE']}', expected '{expected['SIZE']}'"
                    assert disk_info['Rotational'] == expected['Rotational'], \
                        f"{disk_name}: Rotational='{disk_info['Rotational']}', expected '{expected['Rotational']}'"
                checkd_disks += 1
            else:
                assert not (disk_name.startswith('nvme') and 'MODEL' in disk_info), \
                    f"Unexpected NVMe disk: {disk_name}"
                # Skip BMC virtual media devices (sd* with 0 size or "Virtual" in MODEL)
                if disk_name.startswith('sd') and 'MODEL' in disk_info:
                    model = disk_info.get('MODEL', '')
                    size = disk_info.get('SIZE', '0')
                    if 'Virtual' in model or size in ('0B', '0'):
                        continue
                    assert False, f"unexpected sd disk: {disk_name} MODEL={model} SIZE={size}"
        assert checkd_disks == len(disks_to_check), \
            f"Checked {checkd_disks} disks, expected {len(disks_to_check)}"

    def _ctrl_check_sku_32c2_64g12_rc100g1_960g1_7t2(self, sys_info):
        self._ctrl_check_fw(sys_info)
        self._ctrl_check_cpu_32c2(sys_info)
        self._ctrl_check_mem_64g12(sys_info)
        self._ctrl_check_nic_rc100g1_dev2(sys_info)
        self._ctrl_check_disks(sys_info, self._ctrl_get_disks_to_check_960g1_7t2)

    def _ctrl_check_sku_32c2_64g12_rc100g1_960g2_7t2(self, sys_info):
        self._ctrl_check_fw(sys_info)
        self._ctrl_check_cpu_32c2(sys_info)
        self._ctrl_check_mem_64g12(sys_info)
        self._ctrl_check_nic_rc100g1_dev2(sys_info)
        self._ctrl_check_disks(sys_info, self._ctrl_get_disks_to_check_960g2_7t2)

    def _ctrl_check_sku_32c2_64g12_rc100g1_960g2_7t8(self, sys_info):
        self._ctrl_check_fw(sys_info)
        self._ctrl_check_cpu_32c2(sys_info)
        self._ctrl_check_mem_64g12(sys_info)
        self._ctrl_check_nic_rc100g1_dev1(sys_info)
        self._ctrl_check_disks(sys_info, self._ctrl_get_disks_to_check_960g2_7t8)

    def _sku_to_checker(self, sku_str):
        if sku_str == 'ctrl-32c2-64g12-rc100g1-960g1-7t2':
            return self._ctrl_check_sku_32c2_64g12_rc100g1_960g1_7t2
        elif sku_str == 'ctrl-32c2-64g12-rc100g1-960g2-7t2':
            return self._ctrl_check_sku_32c2_64g12_rc100g1_960g2_7t2
        elif sku_str == 'ctrl-32c2-64g12-rc100g1-960g2-7t8':
            return self._ctrl_check_sku_32c2_64g12_rc100g1_960g2_7t8
        return None

    def _fuzz_match_with_mem_disk(self, sys_info):
        min_distance = 3
        min_sku = None
        mem_disk_checkers = [
            [self._ctrl_check_mem_64g12, self._ctrl_get_disks_to_check_960g1_7t2],
            [self._ctrl_check_mem_64g12, self._ctrl_get_disks_to_check_960g2_7t2],
            [self._ctrl_check_mem_64g12, self._ctrl_get_disks_to_check_960g2_7t8],
        ]
        sku_list = self.sku_list()
        for sku_idx, sku in enumerate(sku_list):
            sku_distance = 0
            try:
                mem_disk_checkers[sku_idx][0](sys_info)
            except:
                sku_distance += 1
            try:
                self._ctrl_check_disks(sys_info, mem_disk_checkers[sku_idx][1])
            except:
                sku_distance += 1
            if sku_distance < min_distance:
                min_distance = sku_distance
                min_sku = sku
        return min_sku

    def sku_list(self):
        return [
            'ctrl-32c2-64g12-rc100g1-960g1-7t2',
            'ctrl-32c2-64g12-rc100g1-960g2-7t2',
            'ctrl-32c2-64g12-rc100g1-960g2-7t8',
        ]

    def check(self, sys_info, sku_str):
        checker = self._sku_to_checker(sku_str)
        if not checker:
            return False, f'unknown sku {sku_str}'
        try:
            checker(sys_info)
            return True, ''
        except:
            return False, traceback.format_exc()

    def match(self, sys_info):
        match_fail_info = 'match failed' + os.linesep
        for sku_str in self.sku_list():
            success, check_fail_info = self.check(sys_info, sku_str)
            if success:
                return True, sku_str
            else:
                match_fail_info += f'case {sku_str}:' + os.linesep
                match_fail_info += check_fail_info + os.linesep
        fuzz_match_sku = self._fuzz_match_with_mem_disk(sys_info)
        match_fail_info += f'fuzz match: {fuzz_match_sku}'
        return False, match_fail_info


class CpuSkuSpec:
    def _cpu_check_fw(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line_idx, line in enumerate(dmidecode_lines):
            if 'BIOS Revision: ' in line:
                ver = line.split('BIOS Revision: ')[1]
                assert Version(ver) >= Version('5.27'), \
                    f"BIOS Revision {ver} < 5.27"
            elif 'Firmware Component Name: BMC Firmware' in line:
                next_line = dmidecode_lines[line_idx + 1]
                assert 'Firmware Version' in next_line, \
                    f"BMC FW: expected 'Firmware Version' in '{next_line}'"
                ver = next_line.split()[-1]
                assert Version(ver) >= Version('1.85'), \
                    f"BMC FW Version {ver} < 1.85"

    def _cpu_check_cpu_96c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'AMD EPYC 9654 96-Core Processor', \
            f"CPU: '{cpu['Model name']}', expected 'AMD EPYC 9654 96-Core Processor'"
        assert cpu['Core(s) per socket'] == '96', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 96"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '3707.8120', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 3707.8120"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _cpu_check_mem_64g24(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '1.5T', \
            f"Memory total: {mem['total_capacity']}, expected 1.5T"
        num_memory_devices = 24
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 5600 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 4800, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 4800 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('64 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 64 GB"

    def _cpu_check_nic_rc400g1(self, sys_info):
        num_ib_devices = 1
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        for ib_device_idx in range(num_ib_devices):
            nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
            assert 'Mellanox Technologies MT2910 Family [ConnectX-7]' == nic_model, \
                f"mlx5_{ib_device_idx}: expected ConnectX-7, got '{nic_model}'"
            ib_device_status_info_key = f"CA 'mlx5_{ib_device_idx}'"
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            fw_ver = ib_device_status_info['Firmware version']
            assert Version(fw_ver) >= Version('28.39.3560'), \
                f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.39.3560"
            port_info = ib_device_status_info['Port 1:']
            assert port_info['Rate'] == '400', \
                f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 400"
            assert port_info['State'] == 'Active', \
                f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
            assert port_info['Physical state'] == 'LinkUp', \
                f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            assert port_info['Link layer'] == 'Ethernet', \
                f"mlx5_{ib_device_idx}: Link layer='{port_info['Link layer']}', expected Ethernet"

    def _cpu_get_disks_to_check_3t1_7t1(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 1
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZQL23T8HCLS-00A07', 'SAMSUNG MZQL23T8HCLS-00B7C', 'SOLIDIGM SSDPF2KX038T1'],
                'Firmware': ['GDC5A02Q', 'GDC54C2Q', '9CV10490'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 7681501126656,
                'ModelNumber': ['SAMSUNG MZQL27T6HBLA-00B7C'],
                'Firmware': ['GDC58C2Q'],
            }
        return disks_to_check

    def _cpu_check_disks(self, sys_info, get_disks_to_check):
        disks_to_check = get_disks_to_check()
        checkd_disks = 0
        for disk_info in sys_info['Storage']['block_device']:
            disk_name = disk_info['NAME']
            if disk_name in disks_to_check:
                expected = disks_to_check[disk_name]
                assert disk_info['ModelNumber'] in expected['ModelNumber'], \
                    f"{disk_name}: ModelNumber='{disk_info['ModelNumber']}', expected one of {expected['ModelNumber']}"
                model_idx = expected['ModelNumber'].index(disk_info['ModelNumber'])
                assert disk_info['Firmware'] >= expected['Firmware'][model_idx], \
                    f"{disk_name}: Firmware='{disk_info['Firmware']}' < '{expected['Firmware'][model_idx]}'"
                assert disk_info['PhysicalSize'] == expected['PhysicalSize'], \
                    f"{disk_name}: PhysicalSize={disk_info['PhysicalSize']}, expected {expected['PhysicalSize']}"
                checkd_disks += 1
            else:
                assert not (disk_name.startswith('nvme') and 'MODEL' in disk_info), \
                    f"Unexpected NVMe disk: {disk_name}"
                # Skip BMC virtual media devices (sd* with 0 size or "Virtual" in MODEL)
                if disk_name.startswith('sd') and 'MODEL' in disk_info:
                    model = disk_info.get('MODEL', '')
                    size = disk_info.get('SIZE', '0')
                    if 'Virtual' in model or size in ('0B', '0'):
                        continue
                    assert False, f"unexpected sd disk: {disk_name} MODEL={model} SIZE={size}"
        assert checkd_disks == len(disks_to_check), \
            f"Checked {checkd_disks} disks, expected {len(disks_to_check)}"

    def _cpu_check_sku_96c2_64g24_rc400g1_3t1_7t1(self, sys_info):
        self._cpu_check_fw(sys_info)
        self._cpu_check_cpu_96c2(sys_info)
        self._cpu_check_mem_64g24(sys_info)
        self._cpu_check_nic_rc400g1(sys_info)
        self._cpu_check_disks(sys_info, self._cpu_get_disks_to_check_3t1_7t1)

    def _sku_to_checker(self, sku_str):
        if sku_str == 'cpu-96c2-64g24-rc400g1-3t1-7t1':
            return self._cpu_check_sku_96c2_64g24_rc400g1_3t1_7t1
        return None

    def _fuzz_match_with_mem_disk(self, sys_info):
        min_distance = 3
        min_sku = None
        mem_disk_checkers = [
            [self._cpu_check_mem_64g24, self._cpu_get_disks_to_check_3t1_7t1],
        ]
        sku_list = self.sku_list()
        for sku_idx, sku in enumerate(sku_list):
            sku_distance = 0
            try:
                mem_disk_checkers[sku_idx][0](sys_info)
            except:
                sku_distance += 1
            try:
                self._cpu_check_disks(sys_info, mem_disk_checkers[sku_idx][1])
            except:
                sku_distance += 1
            if sku_distance < min_distance:
                min_distance = sku_distance
                min_sku = sku
        return min_sku

    def sku_list(self):
        return [
            'cpu-96c2-64g24-rc400g1-3t1-7t1',
        ]

    def check(self, sys_info, sku_str):
        checker = self._sku_to_checker(sku_str)
        if not checker:
            return False, f'unknown sku {sku_str}'
        try:
            checker(sys_info)
            return True, ''
        except:
            return False, traceback.format_exc()

    def match(self, sys_info):
        match_fail_info = 'match failed' + os.linesep
        for sku_str in self.sku_list():
            success, check_fail_info = self.check(sys_info, sku_str)
            if success:
                return True, sku_str
            else:
                match_fail_info += f'case {sku_str}:' + os.linesep
                match_fail_info += check_fail_info + os.linesep
        fuzz_match_sku = self._fuzz_match_with_mem_disk(sys_info)
        match_fail_info += f'fuzz match: {fuzz_match_sku}'
        return False, match_fail_info

class B300SkuSpec:
    def _b300_check_fw(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line_idx, line in enumerate(dmidecode_lines):
            if 'BIOS Revision: ' in line:
                ver = line.split('BIOS Revision: ')[1]
                assert Version(ver) >= Version('5.35'), \
                    f"BIOS Revision {ver} < 5.35"
            elif 'Firmware Component Name: BMC Firmware' in line:
                next_line = dmidecode_lines[line_idx + 1]
                assert 'Firmware Version' in next_line, \
                    f"BMC FW: expected 'Firmware Version' in '{next_line}'"
                ver = next_line.split()[-1]
                assert Version(ver) >= Version('13.06'), \
                    f"BMC FW Version {ver} < 13.06"

    def _b300_check_cpu_64c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'Intel(R) Xeon(R) 6767P', \
            f"CPU: '{cpu['Model name']}', expected 'Intel(R) Xeon(R) 6767P'"
        assert cpu['Core(s) per socket'] == '64', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 64"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '3900.0000', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 3900.0000"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _b300_check_mem_96g32(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '3T', \
            f"Memory total: {mem['total_capacity']}, expected 3T"
        num_memory_devices = 32
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 6400, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 6400 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 5200, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 5200 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('96 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 96 GB"

    def _b300_check_gpu_b300x8(self, sys_info):
        accel = sys_info['Accelerator']
        assert accel['gpu_count'] == '8', \
            f"GPU count: {accel['gpu_count']}, expected 8"
        for i, gpu in enumerate(accel['nvidia_info']['gpu']):
            assert gpu['product_name'] == 'NVIDIA B300 SXM6 AC', \
                f"GPU {i}: product_name='{gpu['product_name']}', expected 'NVIDIA B300 SXM6 AC'"
            assert gpu['fb_memory_usage']['total'] == '275040 MiB', \
                f"GPU {i}: fb_memory total='{gpu['fb_memory_usage']['total']}', expected 275040 MiB"
            assert gpu['max_clocks']['graphics_clock'] == '2032 MHz', \
                f"GPU {i}: graphics_clock='{gpu['max_clocks']['graphics_clock']}', expected 2032 MHz"
            assert gpu['max_clocks']['sm_clock'] == '2032 MHz', \
                f"GPU {i}: sm_clock='{gpu['max_clocks']['sm_clock']}', expected 2032 MHz"
            assert gpu['max_clocks']['mem_clock'] == '3996 MHz', \
                f"GPU {i}: mem_clock='{gpu['max_clocks']['mem_clock']}', expected 3996 MHz"
            assert gpu['max_clocks']['video_clock'] == '1860 MHz', \
                f"GPU {i}: video_clock='{gpu['max_clocks']['video_clock']}', expected 1860 MHz"
            assert gpu['gpu_power_readings']['max_power_limit'] == '1100.00 W', \
                f"GPU {i}: max_power_limit='{gpu['gpu_power_readings']['max_power_limit']}', expected 1100.00 W"
            assert gpu['vbios_version'] >= '97.10.4C.00.05', \
                f"GPU {i}: vbios_version='{gpu['vbios_version']}' < 97.10.4C.00.05"
        # In NVIDIA HGX B200/B300 systems, NVSwitches do not appear as PCIe devces.
        # https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html#additional-steps-for-nvidia-hgx-b200-b300-systems

    def _b300_check_nic_ib400g16_rc400g2(self, sys_info):
        num_ib_devices = 22
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        network_nic_mellanox = [x for x in sys_info['Network']['nic'] if x['model'].startswith('Mellanox')]
        for ib_device_idx in range(num_ib_devices):
            ib_device_status_info_key = f"CA 'mlx5_{ib_device_idx}'"
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            port_info = ib_device_status_info['Port 1:']
            assert port_info['State'] == 'Active', \
                f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
            assert port_info['Physical state'] == 'LinkUp', \
                f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            fw_ver = ib_device_status_info['Firmware version']
            link_layer = port_info['Link layer']
            rate = port_info['Rate']
            if ib_device_idx in [4, 17]:
                expected_model = 'Mellanox Technologies MT2910 Family [ConnectX-7]'
                assert expected_model == network_nic_mellanox[ib_device_idx]['model'], \
                    f"mlx5_{ib_device_idx}: model='{network_nic_mellanox[ib_device_idx]['model']}', expected '{expected_model}'"
                assert Version(fw_ver) >= Version('28.39.3560'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.39.3560"
                assert rate == '400', \
                    f"mlx5_{ib_device_idx}: Rate={rate}, expected 400"
                assert link_layer == 'Ethernet', \
                    f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected 'Ethernet'"
            elif ib_device_idx in [11, 12, 13, 14]:
                expected_model = 'Mellanox Technologies MT2910 Family [ConnectX-7]'
                assert expected_model == network_nic_mellanox[ib_device_idx]['model'], \
                    f"mlx5_{ib_device_idx}: model='{network_nic_mellanox[ib_device_idx]['model']}', expected '{expected_model}'"
                assert Version(fw_ver) >= Version('28.46.3022'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.46.3022"
                assert rate == '100', \
                    f"mlx5_{ib_device_idx}: Rate={rate}, expected 100"
                assert link_layer == 'InfiniBand', \
                    f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected 'InfiniBand'"
            else:
                expected_model = 'Mellanox Technologies CX8 Family [ConnectX-8]'
                assert expected_model == network_nic_mellanox[ib_device_idx]['model'], \
                    f"mlx5_{ib_device_idx}: model='{network_nic_mellanox[ib_device_idx]['model']}', expected '{expected_model}'"
                assert Version(fw_ver) >= Version('40.46.3022'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 40.46.3022"
                assert rate == '400', \
                    f"mlx5_{ib_device_idx}: Rate={rate}, expected 400"
                assert link_layer == 'InfiniBand', \
                    f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected 'InfiniBand'"

    def _b300_get_disks_to_check_3t1_3t1(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 1
        for disk_idx in range(num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZ1L23T8HBLA-00A07'],
                'Firmware': ['GDC7202Q'],
            }
        return disks_to_check

    def _b300_check_disks(self, sys_info, get_disks_to_check):
        disks_to_check = get_disks_to_check()
        checkd_disks = 0
        for disk_info in sys_info['Storage']['block_device']:
            disk_name = disk_info['NAME']
            if disk_name in disks_to_check:
                expected = disks_to_check[disk_name]
                assert disk_info['ModelNumber'] in expected['ModelNumber'], \
                    f"{disk_name}: ModelNumber='{disk_info['ModelNumber']}', expected one of {expected['ModelNumber']}"
                model_idx = expected['ModelNumber'].index(disk_info['ModelNumber'])
                assert disk_info['Firmware'] >= expected['Firmware'][model_idx], \
                    f"{disk_name}: Firmware='{disk_info['Firmware']}' < '{expected['Firmware'][model_idx]}'"
                assert disk_info['PhysicalSize'] == expected['PhysicalSize'], \
                    f"{disk_name}: PhysicalSize={disk_info['PhysicalSize']}, expected {expected['PhysicalSize']}"
                checkd_disks += 1
            else:
                assert not (disk_name.startswith('nvme') and 'MODEL' in disk_info), \
                    f"Unexpected NVMe disk: {disk_name}"
                # Skip BMC virtual media devices (sd* with 0 size or "Virtual" in MODEL)
                if disk_name.startswith('sd') and 'MODEL' in disk_info:
                    model = disk_info.get('MODEL', '')
                    size = disk_info.get('SIZE', '0')
                    if 'Virtual' in model or size in ('0B', '0'):
                        continue
                    assert False, f"unexpected sd disk: {disk_name} MODEL={model} SIZE={size}"
        assert checkd_disks == len(disks_to_check), \
            f"Checked {checkd_disks} disks, expected {len(disks_to_check)}"

    def _b300_check_sku_64c2_96g32_b300x8_ib400g16_rc400g2_3t1_3t1(self, sys_info):
        self._b300_check_fw(sys_info)
        self._b300_check_cpu_64c2(sys_info)
        self._b300_check_mem_96g32(sys_info)
        self._b300_check_gpu_b300x8(sys_info)
        self._b300_check_nic_ib400g16_rc400g2(sys_info)
        self._b300_check_disks(sys_info, self._b300_get_disks_to_check_3t1_3t1)

    def _sku_to_checker(self, sku_str):
        if sku_str == 'b300-64c2-96g32-b300x8-ib400g16-rc400g2-3t1-3t1':
            return self._b300_check_sku_64c2_96g32_b300x8_ib400g16_rc400g2_3t1_3t1
        return None

    def _fuzz_match_with_mem_disk(self, sys_info):
        min_distance = 3
        min_sku = None
        mem_disk_checkers = [
            [self._b300_check_mem_96g32, self._b300_get_disks_to_check_3t1_3t1],
        ]
        sku_list = self.sku_list()
        for sku_idx, sku in enumerate(sku_list):
            sku_distance = 0
            try:
                mem_disk_checkers[sku_idx][0](sys_info)
            except:
                sku_distance += 1
            try:
                self._b300_check_disks(sys_info, mem_disk_checkers[sku_idx][1])
            except:
                sku_distance += 1
            if sku_distance < min_distance:
                min_distance = sku_distance
                min_sku = sku
        return min_sku

    def sku_list(self):
        return [
            'b300-64c2-96g32-b300x8-ib400g16-rc400g2-3t1-3t1',
        ]

    def check(self, sys_info, sku_str):
        checker = self._sku_to_checker(sku_str)
        if not checker:
            return False, f'unknown sku {sku_str}'
        try:
            checker(sys_info)
            return True, ''
        except:
            return False, traceback.format_exc()

    def match(self, sys_info):
        match_fail_info = 'match failed' + os.linesep
        for sku_str in self.sku_list():
            success, check_fail_info = self.check(sys_info, sku_str)
            if success:
                return True, sku_str
            else:
                match_fail_info += f'case {sku_str}:' + os.linesep
                match_fail_info += check_fail_info + os.linesep
        fuzz_match_sku = self._fuzz_match_with_mem_disk(sys_info)
        match_fail_info += f'fuzz match: {fuzz_match_sku}'
        return False, match_fail_info


class StorageSkuSpec:
    def _storage_check_fw_superfusion(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line in enumerate(dmidecode_lines):
            if 'BIOS Revision: ' in line:
                ver = line.split('BIOS Revision: ')[1]
                assert Version(ver) >= Version('1.83'), \
                    f"BIOS Revision {ver} < 1.83"
                break

    def _storage_check_fw_inspur(self, sys_info):
        dmidecode_lines = sys_info['System']['dmidecode'].splitlines()
        for line_idx, line in enumerate(dmidecode_lines):
            if 'BIOS Information' in line:
                ver_line = dmidecode_lines[line_idx + 2]
                assert 'Version:' in ver_line, \
                    f"BIOS: expected 'Version:' in '{ver_line}'"
                ver = ver_line.split()[-1]
                assert Version(ver) >= Version('04.00.09'), \
                    f"BIOS Version {ver} < 04.00.09"
            elif 'Firmware Component Name: BMC Firmware' in line:
                next_line = dmidecode_lines[line_idx + 1]
                assert 'Firmware Version' in next_line, \
                    f"BMC FW: expected 'Firmware Version' in '{next_line}'"
                ver = next_line.split()[-1]
                assert Version(ver) >= Version('4.33'), \
                    f"BMC FW Version {ver} < 4.33"

    def _storage_check_cpu_28c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz', \
            f"CPU: '{cpu['Model name']}', expected 'Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz'"
        assert cpu['Core(s) per socket'] == '28', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 28"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '3100.0000', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 3100.0000"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _storage_check_cpu_128c2(self, sys_info):
        cpu = sys_info['CPU']
        assert cpu['Model name'] == 'Intel(R) Xeon(R) 6980P', \
            f"CPU: '{cpu['Model name']}', expected 'Intel(R) Xeon(R) 6980P'"
        assert cpu['Core(s) per socket'] == '128', \
            f"CPU cores/socket: {cpu['Core(s) per socket']}, expected 128"
        assert cpu['Socket(s)'] == '2', \
            f"CPU sockets: {cpu['Socket(s)']}, expected 2"
        assert cpu['CPU max MHz'] == '3900.0000', \
            f"CPU max MHz: {cpu['CPU max MHz']}, expected 3900.0000"
        assert cpu['NUMA node(s)'] == '2', \
            f"NUMA nodes: {cpu['NUMA node(s)']}, expected 2"

    def _storage_check_mem_64g24_ddr4(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '1.5T', \
            f"Memory total: {mem['total_capacity']}, expected 1.5T"
        num_memory_devices = 24
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR4', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR4"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 3200, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 3200 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 2933, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 2933 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('64 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 64 GB"

    def _storage_check_mem_64g24_ddr5(self, sys_info):
        mem = sys_info['Memory']
        assert mem['total_capacity'] == '1.5T', \
            f"Memory total: {mem['total_capacity']}, expected 1.5T"
        num_memory_devices = 24
        assert len(mem['memory_devices']) == num_memory_devices, \
            f"Memory devices: {len(mem['memory_devices'])}, expected {num_memory_devices}"
        for i, dev in enumerate(mem['memory_devices']):
            assert dev['Type'] == 'DDR5', \
                f"DIMM {i}: Type='{dev['Type']}', expected DDR5"
            assert dev['Manufacturer'] in ['Samsung', 'SK Hynix', 'Hynix', 'Micron Technology'], \
                f"DIMM {i}: Manufacturer='{dev['Manufacturer']}'"
            assert int(dev['Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Speed='{dev['Speed']}', expected >= 5600 MT/s"
            assert dev['Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Speed unit mismatch"
            assert int(dev['Configured Memory Speed'].split()[0]) >= 5600, \
                f"DIMM {i}: Configured Speed='{dev['Configured Memory Speed']}', expected >= 5600 MT/s"
            assert dev['Configured Memory Speed'].split()[1] == 'MT/s', \
                f"DIMM {i}: Configured Speed unit mismatch"
            assert dev['Volatile Size'].strip().endswith('64 GB'), \
                f"DIMM {i}: Volatile Size='{dev['Volatile Size']}', expected 64 GB"

    def _storage_check_nic_rc400g1(self, sys_info):
        num_ib_devices = 5
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        for ib_device_idx in range(num_ib_devices):
            ib_device_status_info_key = f"CA 'mlx5_{ib_device_idx}'"
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            port_info = ib_device_status_info['Port 1:']
            fw_ver = ib_device_status_info['Firmware version']
            link_layer = port_info['Link layer']
            if ib_device_idx in [0, 1]:
                nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
                assert 'Mellanox Technologies MT2894 Family [ConnectX-6 Lx]' == nic_model, \
                    f"mlx5_{ib_device_idx}: expected ConnectX-6 Lx, got '{nic_model}'"
                assert Version(fw_ver) >= Version('22.43.2566'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 22.43.2566"
                assert port_info['Rate'] == '40', \
                    f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 40"
                assert port_info['State'] == 'Down', \
                    f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Down"
                assert port_info['Physical state'] == 'Disabled', \
                    f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected Disabled"
            elif ib_device_idx in [2, 3]:
                nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
                assert 'Mellanox Technologies MT27710 Family [ConnectX-4 Lx]' == nic_model, \
                    f"mlx5_{ib_device_idx}: expected ConnectX-4 Lx, got '{nic_model}'"
                assert Version(fw_ver) >= Version('14.32.1010'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 14.32.1010"
                assert port_info['Rate'] == '40', \
                    f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 40"
                assert port_info['State'] == 'Down', \
                    f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Down"
                assert port_info['Physical state'] == 'Disabled', \
                    f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected Disabled"
            else:
                nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
                assert 'Mellanox Technologies MT2910 Family [ConnectX-7]' == nic_model, \
                    f"mlx5_{ib_device_idx}: expected ConnectX-7, got '{nic_model}'"
                assert Version(fw_ver) >= Version('28.43.2566'), \
                    f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.43.2566"
                assert port_info['Rate'] == '400', \
                    f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 400"
                assert port_info['State'] == 'Active', \
                    f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
                assert port_info['Physical state'] == 'LinkUp', \
                    f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            assert link_layer == 'Ethernet', \
                f"mlx5_{ib_device_idx}: Link layer='{link_layer}', expected Ethernet"

    def _storage_check_nic_rc400g4(self, sys_info):
        num_ib_devices = 4
        assert len(sys_info['Network']['ib']['ib_device_status']) == num_ib_devices, \
            f"Expected {num_ib_devices} IB devices, got {len(sys_info['Network']['ib']['ib_device_status'])}"
        for ib_device_idx in range(num_ib_devices):
            nic_model = sys_info['Network']['nic'][ib_device_idx]['model']
            assert 'Mellanox Technologies MT2910 Family [ConnectX-7]' == nic_model, \
                f"mlx5_{ib_device_idx}: expected ConnectX-7, got '{nic_model}'"
            ib_device_status_info_key = f"CA 'mlx5_{ib_device_idx}'"
            ib_device_status_info = sys_info['Network']['ib']['ib_device_status'][ib_device_status_info_key]
            fw_ver = ib_device_status_info['Firmware version']
            assert Version(fw_ver) >= Version('28.46.3048'), \
                f"mlx5_{ib_device_idx}: firmware {fw_ver} < 28.46.3048"
            port_info = ib_device_status_info['Port 1:']
            assert port_info['Rate'] == '400', \
                f"mlx5_{ib_device_idx}: Rate={port_info['Rate']}, expected 400"
            assert port_info['State'] == 'Active', \
                f"mlx5_{ib_device_idx}: State={port_info['State']}, expected Active"
            assert port_info['Physical state'] == 'LinkUp', \
                f"mlx5_{ib_device_idx}: Physical state={port_info['Physical state']}, expected LinkUp"
            assert port_info['Link layer'] == 'Ethernet', \
                f"mlx5_{ib_device_idx}: Link layer='{port_info['Link layer']}', expected Ethernet"
        assert 3 == sys_info['PCIe']['pcie_info'].count('Part number: MCX75310AAC-NEAT'), \
            f"Expected 3 MCX75310AAC-NEAT, got {sys_info['PCIe']['pcie_info'].count('Part number: MCX75310AAC-NEAT')}"
        assert 1 == sys_info['PCIe']['pcie_info'].count('Part number: MCX75310AAS-NEAT'), \
            f"Expected 1 MCX75310AAS-NEAT, got {sys_info['PCIe']['pcie_info'].count('Part number: MCX75310AAS-NEAT')}"

    def _storage_get_disks_to_check_480g1_7t8(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 8
        for disk_idx in range(num_os_disks):
            disks_to_check['sda'] = {
                'SIZE': '894.2G',
                'Rotational': '0',
            }
        for disk_idx in range(num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 7681501126656,
                'ModelNumber': ['INTEL SSDPF2KX076T1'],
                'Firmware': ['9CV10490'],
            }
        return disks_to_check

    def _storage_get_disks_to_check_960g1_3t20(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 20
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZ1L2960HCJR-00A07'],
                'Firmware': ['GDC7802Q'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 3840755982336,
                'ModelNumber': ['SAMSUNG MZQL23T8HCLS-00A07', 'SAMSUNG MZQL23T8HCLS-00B7C', 'SOLIDIGM SSDPF2KX038T1'],
                'Firmware': ['GDC5A02Q', 'GDC54C2Q', '9CV10490'],
            }
        return disks_to_check

    def _storage_get_disks_to_check_960g1_15t20(self):
        disks_to_check = {}
        num_os_disks = 1
        num_data_disks = 20
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZ1L2960HCJR-00A07'],
                'Firmware': ['GDC7802Q'],
            }
        for disk_idx in range(num_os_disks, num_os_disks + num_data_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 15360950534144,
                'ModelNumber': ['SAMSUNG MZWL615THBLF-00B07', 'SOLIDIGM SB5PH27X153T', 'YMTC YMES3XJ17H18SE'],
                'Firmware': ['LDD5702Q', 'G70YG150', 'YM19E110'],
            }
        return disks_to_check

    def _storage_get_disks_to_check_960g1(self):
        disks_to_check = {}
        num_os_disks = 1
        for disk_idx in range(num_os_disks):
            disks_to_check[f'nvme{disk_idx}n1'] = {
                'PhysicalSize': 960197124096,
                'ModelNumber': ['SAMSUNG MZ1L2960HCJR-00A07'],
                'Firmware': ['GDC7802Q'],
            }
        return disks_to_check

    def _storage_check_disks(self, sys_info, get_disks_to_check):
        disks_to_check = get_disks_to_check()
        checkd_disks = 0
        for disk_info in sys_info['Storage']['block_device']:
            disk_name = disk_info['NAME']
            if disk_name in disks_to_check:
                expected = disks_to_check[disk_name]
                if disk_name.startswith('nvme'):
                    assert disk_info['ModelNumber'] in expected['ModelNumber'], \
                        f"{disk_name}: ModelNumber='{disk_info['ModelNumber']}', expected one of {expected['ModelNumber']}"
                    model_idx = expected['ModelNumber'].index(disk_info['ModelNumber'])
                    assert disk_info['Firmware'] >= expected['Firmware'][model_idx], \
                        f"{disk_name}: Firmware='{disk_info['Firmware']}' < '{expected['Firmware'][model_idx]}'"
                    assert disk_info['PhysicalSize'] == expected['PhysicalSize'], \
                        f"{disk_name}: PhysicalSize={disk_info['PhysicalSize']}, expected {expected['PhysicalSize']}"
                else:
                    assert disk_info['SIZE'] == expected['SIZE'], \
                        f"{disk_name}: SIZE='{disk_info['SIZE']}', expected '{expected['SIZE']}'"
                    assert disk_info['Rotational'] == expected['Rotational'], \
                        f"{disk_name}: Rotational='{disk_info['Rotational']}', expected '{expected['Rotational']}'"
                checkd_disks += 1
            else:
                assert not (disk_name.startswith('nvme') and 'MODEL' in disk_info), \
                    f"Unexpected NVMe disk: {disk_name}"
                # Skip BMC virtual media devices (sd* with 0 size or "Virtual" in MODEL)
                if disk_name.startswith('sd') and 'MODEL' in disk_info:
                    model = disk_info.get('MODEL', '')
                    size = disk_info.get('SIZE', '0')
                    if 'Virtual' in model or size in ('0B', '0'):
                        continue
                    assert False, f"unexpected sd disk: {disk_name} MODEL={model} SIZE={size}"
        assert checkd_disks == len(disks_to_check), \
            f"Checked {checkd_disks} disks, expected {len(disks_to_check)}"

    def _storage_check_sku_28c2_64g24_rc400g1_480g1_7t8(self, sys_info):
        self._storage_check_fw_superfusion(sys_info)
        self._storage_check_cpu_28c2(sys_info)
        self._storage_check_mem_64g24_ddr4(sys_info)
        self._storage_check_nic_rc400g1(sys_info)
        self._storage_check_disks(sys_info, self._storage_get_disks_to_check_480g1_7t8)

    def _storage_check_sku_128c2_64g24_rc400g4_960g1_3t20(self, sys_info):
        self._storage_check_fw_inspur(sys_info)
        self._storage_check_cpu_128c2(sys_info)
        self._storage_check_mem_64g24_ddr5(sys_info)
        self._storage_check_nic_rc400g4(sys_info)
        self._storage_check_disks(sys_info, self._storage_get_disks_to_check_960g1_3t20)

    def _storage_check_sku_128c2_64g24_rc400g4_960g1_15t20(self, sys_info):
        self._storage_check_fw_inspur(sys_info)
        self._storage_check_cpu_128c2(sys_info)
        self._storage_check_mem_64g24_ddr5(sys_info)
        self._storage_check_nic_rc400g4(sys_info)
        self._storage_check_disks(sys_info, self._storage_get_disks_to_check_960g1_15t20)

    def _storage_check_sku_128c2_64g24_rc400g4_960g1(self, sys_info):
        self._storage_check_fw_inspur(sys_info)
        self._storage_check_cpu_128c2(sys_info)
        self._storage_check_mem_64g24_ddr5(sys_info)
        self._storage_check_nic_rc400g4(sys_info)
        self._storage_check_disks(sys_info, self._storage_get_disks_to_check_960g1)

    def _sku_to_checker(self, sku_str):
        if sku_str == 'storage-28c2-64g24-rc400g1-480g1-7t8':
            return self._storage_check_sku_28c2_64g24_rc400g1_480g1_7t8
        elif sku_str == 'storage-128c2-64g24-rc400g4-960g1-3t20':
            return self._storage_check_sku_128c2_64g24_rc400g4_960g1_3t20
        elif sku_str == 'storage-128c2-64g24-rc400g4-960g1-15t20':
            return self._storage_check_sku_128c2_64g24_rc400g4_960g1_15t20
        elif sku_str == 'storage-128c2-64g24-rc400g4-960g1':
            return self._storage_check_sku_128c2_64g24_rc400g4_960g1
        return None

    def _fuzz_match_with_mem_disk(self, sys_info):
        min_distance = 3
        min_sku = None
        mem_disk_checkers = [
            [self._storage_check_mem_64g24_ddr4, self._storage_get_disks_to_check_480g1_7t8],
            [self._storage_check_mem_64g24_ddr5, self._storage_get_disks_to_check_960g1_3t20],
            [self._storage_check_mem_64g24_ddr5, self._storage_get_disks_to_check_960g1_15t20],
            [self._storage_check_mem_64g24_ddr5, self._storage_get_disks_to_check_960g1],
        ]
        sku_list = self.sku_list()
        for sku_idx, sku in enumerate(sku_list):
            sku_distance = 0
            try:
                mem_disk_checkers[sku_idx][0](sys_info)
            except:
                sku_distance += 1
            try:
                self._storage_check_disks(sys_info, mem_disk_checkers[sku_idx][1])
            except:
                sku_distance += 1
            if sku_distance < min_distance:
                min_distance = sku_distance
                min_sku = sku
        return min_sku

    def sku_list(self):
        return [
            'storage-28c2-64g24-rc400g1-480g1-7t8',
            'storage-128c2-64g24-rc400g4-960g1-3t20',
            'storage-128c2-64g24-rc400g4-960g1-15t20',
            'storage-128c2-64g24-rc400g4-960g1',
        ]

    def check(self, sys_info, sku_str):
        checker = self._sku_to_checker(sku_str)
        if not checker:
            return False, f'unknown sku {sku_str}'
        try:
            checker(sys_info)
            return True, ''
        except:
            return False, traceback.format_exc()

    def match(self, sys_info):
        match_fail_info = 'match failed' + os.linesep
        for sku_str in self.sku_list():
            success, check_fail_info = self.check(sys_info, sku_str)
            if success:
                return True, sku_str
            else:
                match_fail_info += f'case {sku_str}:' + os.linesep
                match_fail_info += check_fail_info + os.linesep
        fuzz_match_sku = self._fuzz_match_with_mem_disk(sys_info)
        match_fail_info += f'fuzz match: {fuzz_match_sku}'
        return False, match_fail_info

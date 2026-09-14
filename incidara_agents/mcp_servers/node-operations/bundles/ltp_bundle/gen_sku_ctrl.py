import argparse
import json

from sku_spec import CtrlSkuSpec


def main():
    parser = argparse.ArgumentParser(description='Generate SKU string from superbench system info JSON')
    parser.add_argument(
        '--json',
        type=str,
        required=True,
        help='Path to superbench system info JSON file'
    )
    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Output file for SKU string generated'
    )
    args = parser.parse_args()
    with open(args.json, 'r') as f:
        sys_info = json.load(f)
    ctrl_sku_spec = CtrlSkuSpec()
    success, sku_str = ctrl_sku_spec.match(sys_info)
    with open(args.output, 'w') as f:
        f.write(sku_str)

if __name__ == '__main__':
    main()

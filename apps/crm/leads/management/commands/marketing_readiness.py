from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from leads.readiness import collect_marketing_readiness


class Command(BaseCommand):
    help = '只读检查 CRM、Meta、WhatsApp、Google 营销链路配置；不会发送事件或输出个人数据/密钥。'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json', help='输出 JSON')
        parser.add_argument(
            '--network',
            action='store_true',
            help='额外执行现有只读平台诊断（GET/令牌刷新）；仍不会发送广告事件',
        )
        parser.add_argument(
            '--strict',
            action='store_true',
            help='发现 FAIL 或 WARN 时以非零状态退出，供部署流水线使用',
        )

    def handle(self, *args, **options):
        report = collect_marketing_readiness(include_network=bool(options['network']))
        if options['as_json']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self.stdout.write(f'营销链路只读体检：{report["overall"].upper()}')
            self.stdout.write(f'模式：{report["mode"]}')
            for check in report['checks']:
                self.stdout.write(
                    f'[{check["status"].upper():4}] {check["label"]}: {check["detail"]}'
                )
            summary = report['summary']
            self.stdout.write(
                f'合计：PASS {summary["pass"]} / WARN {summary["warn"]} / FAIL {summary["fail"]}'
            )
            self.stdout.write(report['acceptance_note'])

        if options['strict'] and report['overall'] != 'pass':
            raise CommandError(f'营销链路体检未通过：{report["overall"]}')

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase

from console.content_access import RELEASES_READ
from console.payloads import releases_payload
from console.three_d_config import purpose_label
from console.three_d_payloads import _preview_build_guidance


class ReleaseUiSemanticsTests(SimpleTestCase):
    @staticmethod
    def _empty_build_queryset():
        queryset = MagicMock()
        queryset.filter.return_value.order_by.return_value.__getitem__.return_value = []
        return queryset

    @staticmethod
    def _empty_release_queryset():
        queryset = MagicMock()
        queryset.order_by.return_value.__getitem__.return_value = []
        return queryset

    def _payload(self, *, include_diagnostics: bool):
        site = SimpleNamespace(name='测试站点', code='siteos_demo')
        with (
            patch(
                'console.payloads.ReleaseBuild.objects.select_related',
                return_value=self._empty_build_queryset(),
            ),
            patch(
                'console.payloads.Release.objects.filter',
                return_value=self._empty_release_queryset(),
            ),
        ):
            return releases_payload(
                site=site,
                capabilities=frozenset({RELEASES_READ}),
                include_diagnostics=include_diagnostics,
            )

    def test_release_page_names_the_controlled_deployment_boundary(self):
        payload = self._payload(include_diagnostics=False)

        self.assertEqual(payload['title'], '预览与快照')
        self.assertEqual(payload['workspaceLabel'], '预览与快照')
        self.assertIn('独立部署权限和二次确认', payload['description'])
        self.assertIn('不会自动部署或改动其它环境', payload['description'])
        self.assertIn('生产发布由受控部署流程执行', payload['notes'][0]['body'])

    def test_release_search_only_promises_diagnostics_to_system_admins(self):
        ordinary = self._payload(include_diagnostics=False)
        diagnostics = self._payload(include_diagnostics=True)

        self.assertEqual(ordinary['searchPlaceholder'], '搜索构建键或快照键')
        self.assertEqual(
            ordinary['views']['builds']['searchPlaceholder'],
            '搜索构建键或快照键',
        )
        self.assertEqual(
            ordinary['views']['releases']['searchPlaceholder'],
            '搜索快照键、操作者或备注',
        )
        for placeholder in (
            ordinary['searchPlaceholder'],
            ordinary['views']['builds']['searchPlaceholder'],
            ordinary['views']['releases']['searchPlaceholder'],
        ):
            self.assertNotIn('产物路径', placeholder)
            self.assertNotIn('日志', placeholder)

        self.assertIn('产物路径', diagnostics['views']['builds']['searchPlaceholder'])
        self.assertIn('日志摘要', diagnostics['views']['builds']['searchPlaceholder'])
        self.assertIn('产物路径', diagnostics['views']['releases']['searchPlaceholder'])


class ThreeDUiSemanticsTests(SimpleTestCase):
    template_root = Path(settings.BASE_DIR) / 'console' / 'templates' / 'console'

    def test_profile_purpose_has_operator_facing_chinese_labels(self):
        self.assertEqual(purpose_label(None), '通用展示')
        self.assertEqual(purpose_label('general'), '通用展示')
        self.assertEqual(purpose_label('homepage_hero'), '首页 Hero 背景')
        self.assertEqual(
            purpose_label('homepage_equipment_preview'),
            '首页 3D 设备预览',
        )
        self.assertEqual(purpose_label('custom_slot'), 'custom_slot')

    def test_preview_guidance_is_neutral_instead_of_a_permanent_failed_check(self):
        self.assertIn('可生成新的预览产物', _preview_build_guidance(True))
        self.assertIn('具备预览构建权限的成员', _preview_build_guidance(False))

        payload_source = (
            Path(settings.BASE_DIR) / 'console' / 'three_d_payloads.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn("{'label': '修改后需生成预览产物', 'ok': False}", payload_source)

    def test_read_only_empty_state_does_not_instruct_the_operator_to_write(self):
        workspace_html = get_template('console/_three_d_workspace.html').render(
            {
                'three_d_workspace': {
                    'permissions': {
                        'canWrite': False,
                        'canImportLocal': False,
                        'canBuildPreview': False,
                    },
                    'assets': [],
                    'profiles': [],
                    'placements': [],
                    'assetCount': 0,
                    'profileCount': 0,
                    'placementCount': 0,
                    'unboundSlotCount': 2,
                    'checks': [],
                    'previewGuidance': _preview_build_guidance(False),
                    'assetsUrl': '/admin/assets/',
                }
            }
        )
        detail_html = get_template('console/_three_d_asset_detail_workspace.html').render(
            {
                'three_d_asset_detail': {
                    'permissions': {'canWrite': False, 'canBuildPreview': False},
                    'asset': {
                        'name': '测试模型',
                        'statusLabel': '启用',
                        'ext': '.glb',
                        'size': '0 B',
                        'mimeType': 'model/gltf-binary',
                        'publicPath': '/media/test.glb',
                    },
                    'profiles': [],
                    'placements': [],
                    'profileCount': 0,
                    'placementCount': 0,
                    'workspaceUrl': '/admin/assets/3d/',
                }
            }
        )

        self.assertIn('当前账号仅可查看；需要新增时', workspace_html)
        self.assertIn('当前账号仅可查看；需要新增时', detail_html)
        self.assertNotIn('基于此资产创建配置', detail_html)

    def test_embedded_three_d_workspaces_do_not_nest_main_landmarks(self):
        templates = (
            '_three_d_workspace.html',
            '_three_d_asset_detail_workspace.html',
            '_three_d_profile_editor_workspace.html',
            '_three_d_placement_editor_workspace.html',
        )
        for name in templates:
            with self.subTest(template=name):
                source = (self.template_root / name).read_text(encoding='utf-8')
                self.assertNotIn('<main class="three-d-', source)
                self.assertNotIn('</main>', source)

        workspace_source = (self.template_root / '_three_d_workspace.html').read_text(
            encoding='utf-8'
        )
        detail_source = (
            self.template_root / '_three_d_asset_detail_workspace.html'
        ).read_text(encoding='utf-8')
        self.assertIn('{{ profile.purposeLabel }}', workspace_source)
        self.assertIn('{{ profile.purposeLabel }}', detail_source)

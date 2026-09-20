from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase

from console.content_access import (
    ASSETS_READ,
    CONTENT_READ,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
)
from console.payloads import _apply_table_contract, assets_payload, releases_payload


STATIC_ROOT = Path(settings.BASE_DIR) / 'console' / 'static' / 'console'
TEMPLATE_ROOT = Path(settings.BASE_DIR) / 'console' / 'templates' / 'console'


class _ElementRecorder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(self, tag, attrs):
        normalized = dict(attrs)
        element_id = normalized.get('id')
        if element_id:
            self.elements[element_id] = (tag, normalized)


class GenericTableRendererContractTests(SimpleTestCase):
    def setUp(self):
        self.script = (STATIC_ROOT / 'admin-shell.js').read_text(encoding='utf-8')

    def test_selection_requires_an_explicit_flag_and_at_least_one_bulk_action(self):
        selectable_function = self.script.split('function tableIsSelectable()', 1)[1].split(
            'function tableHasRowActions()',
            1,
        )[0]

        self.assertIn("activeTable().selectable === true", selectable_function)
        self.assertIn('tableBulkActions().length > 0', selectable_function)
        self.assertIn('if (selectable) {', self.script)
        self.assertIn("input.name = 'record_ids'", self.script)

    def test_action_column_is_derived_from_the_complete_active_table(self):
        action_function = self.script.split('function tableHasRowActions()', 1)[1].split(
            'function visibleTableColumns()',
            1,
        )[0]
        row_action_renderer = self.script.split('function renderRowActions(row)', 1)[1].split(
            'function renderTable(rows)',
            1,
        )[0]

        self.assertIn("(activeTable().rows || []).some", action_function)
        self.assertIn("(row.actions || []).length > 0", action_function)
        self.assertNotIn('EMPTY_TEXT', row_action_renderer)
        self.assertGreaterEqual(self.script.count('if (showActions) {'), 3)

    def test_responsive_columns_are_filtered_before_width_and_dom_are_built(self):
        visible_columns = self.script.split('function visibleTableColumns()', 1)[1].split(
            'function activeFilterGroups()',
            1,
        )[0]

        self.assertIn('Number(column.hideBelow || 0)', visible_columns)
        self.assertIn('window.innerWidth >= breakpoint', visible_columns)
        self.assertIn('tableMinWidth(columns, {selectable, showActions})', self.script)


class GenericTablePayloadContractTests(SimpleTestCase):
    def test_contract_defaults_every_top_level_and_tabbed_table_to_non_selectable(self):
        payload = {
            'table': {'columns': [], 'rows': []},
            'views': {
                'one': {'table': {'columns': [], 'rows': []}},
                'summary': {'table': None},
            },
        }

        result = _apply_table_contract(payload)

        self.assertFalse(result['table']['selectable'])
        self.assertEqual(result['table']['bulkActions'], [])
        self.assertFalse(result['views']['one']['table']['selectable'])
        self.assertEqual(result['views']['one']['table']['bulkActions'], [])

    def test_contract_preserves_an_explicit_future_bulk_action(self):
        action = {'kind': 'post', 'label': '批量归档', 'href': '/batch/archive/'}
        payload = {
            'table': {
                'selectable': True,
                'bulkActions': (action,),
            },
        }

        result = _apply_table_contract(payload)

        self.assertTrue(result['table']['selectable'])
        self.assertEqual(result['table']['bulkActions'], [action])

    def test_asset_payload_has_compact_widths_and_hides_only_secondary_recency_at_1280(self):
        site = SimpleNamespace(pk=1, id=1, code='siteos_demo', name='New Crown')
        asset = SimpleNamespace(
            id=7,
            title='Bottle line render',
            original_name='bottle-line.webp',
            public_path='/assets/bottle-line.webp',
            storage_path='managed/siteos_demo/bottle-line.webp',
            asset_type='image',
            status='active',
            file_size_bytes=2048,
            mime_type='image/webp',
            updated_at=None,
        )
        asset_rows = MagicMock()
        asset_rows.order_by.return_value.__getitem__.return_value = [asset]

        def filtered_assets(**kwargs):
            if kwargs == {'site': site}:
                return asset_rows
            count_rows = MagicMock()
            count_rows.count.return_value = 1
            return count_rows

        with patch('console.payloads.MediaAsset.objects.filter', side_effect=filtered_assets):
            payload = assets_payload(site=site, capabilities=frozenset({ASSETS_READ}))

        table = payload['table']
        updated_column = next(column for column in table['columns'] if column['key'] == 'updatedAt')
        self.assertFalse(table['selectable'])
        self.assertEqual(table['bulkActions'], [])
        self.assertEqual(table['actionWidth'], 112)
        self.assertEqual(updated_column['hideBelow'], 1320)
        self.assertLessEqual(sum(table['columnWidths'].values()) + table['actionWidth'], 1010)
        self.assertIn(
            {'label': '最后更新', 'value': '未设置'},
            table['rows'][0]['details'],
        )

    def test_release_tabs_are_explicitly_non_selectable_and_have_no_row_actions(self):
        site = SimpleNamespace(pk=1, id=1, code='siteos_demo', name='New Crown')
        build_rows = MagicMock()
        build_rows.filter.return_value.order_by.return_value.__getitem__.return_value = []
        release_rows = MagicMock()
        release_rows.order_by.return_value.__getitem__.return_value = []

        with (
            patch('console.payloads.ReleaseBuild.objects.select_related', return_value=build_rows),
            patch('console.payloads.Release.objects.filter', return_value=release_rows),
        ):
            payload = releases_payload(site=site, capabilities=frozenset({RELEASES_READ}))

        for view in payload['views'].values():
            table = view['table']
            self.assertFalse(table['selectable'])
            self.assertEqual(table['bulkActions'], [])
            self.assertTrue(all(not row['actions'] for row in table['rows']))

    def test_article_preview_actions_require_all_three_capabilities(self):
        site = SimpleNamespace(pk=1, id=1, code='siteos_demo', name='Vorntek')
        release = SimpleNamespace(
            id=9,
            release_key='article-preview:1:v1',
            status='built',
            created_by='reviewer',
            notes='Private preview',
            artifact_path='',
            exported_at=None,
            built_at=None,
            published_at=None,
            created_at=None,
            snapshot_manifest={'kind': 'articlePreview', 'version': 'v1'},
        )

        def payload_for(capabilities):
            build_rows = MagicMock()
            build_rows.filter.return_value.order_by.return_value.__getitem__.return_value = []
            release_rows = MagicMock()
            release_rows.order_by.return_value.__getitem__.return_value = [release]
            with (
                patch('console.payloads.ReleaseBuild.objects.select_related', return_value=build_rows),
                patch('console.payloads.Release.objects.filter', return_value=release_rows),
            ):
                return releases_payload(site=site, capabilities=frozenset(capabilities))

        incomplete = payload_for({RELEASES_READ, RELEASES_PREVIEW_BUILD})
        complete = payload_for({CONTENT_READ, RELEASES_READ, RELEASES_PREVIEW_BUILD})

        self.assertNotIn('生成文章私有预览', [action['label'] for action in incomplete['actions']])
        self.assertEqual(incomplete['views']['releases']['table']['rows'][0]['actions'], [])
        self.assertIn('生成文章私有预览', [action['label'] for action in complete['actions']])
        self.assertEqual(
            complete['views']['releases']['table']['rows'][0]['actions'][0]['label'],
            '打开文章私有预览',
        )


class ContentAccessibilityDomContractTests(SimpleTestCase):
    def test_detail_drawer_has_modal_name_description_and_focus_contract(self):
        template = (TEMPLATE_ROOT / 'app_shell.html').read_text(encoding='utf-8')
        recorder = _ElementRecorder()
        recorder.feed(template)
        tag, attrs = recorder.elements['detailDrawer']
        script = (STATIC_ROOT / 'admin-shell.js').read_text(encoding='utf-8')

        self.assertEqual(tag, 'aside')
        self.assertEqual(attrs['role'], 'dialog')
        self.assertEqual(attrs['aria-modal'], 'true')
        self.assertEqual(attrs['aria-labelledby'], 'detailTitle')
        self.assertEqual(attrs['aria-describedby'], 'detailSubtitle')
        self.assertEqual(attrs['aria-hidden'], 'true')
        self.assertEqual(attrs['tabindex'], '-1')
        self.assertIn("app.setAttribute('inert', '')", script)
        self.assertIn("document.getElementById('closeDetail')?.focus", script)
        self.assertIn('returnTarget.focus({preventScroll: true})', script)

    def test_article_filters_and_taxonomy_expose_pressed_and_named_states(self):
        script = (STATIC_ROOT / 'article-workspace.js').read_text(encoding='utf-8')

        self.assertIn("group.setAttribute('aria-label', `${label}筛选`)", script)
        self.assertIn("button.setAttribute('aria-pressed', String(activeKey === option.key))", script)
        self.assertIn("button.setAttribute('aria-pressed', String(active))", script)
        self.assertIn(
            "checkbox.setAttribute('aria-label', `选择分类 ${node.trailLabel || node.name}`)",
            script,
        )
        self.assertIn(
            "button.setAttribute('aria-pressed', String(state.selectedTaxa.has(String(node.id))))",
            script,
        )
        self.assertIn("toggle.setAttribute('aria-expanded'", script)


class ThreeDResponsiveTableContractTests(SimpleTestCase):
    @staticmethod
    def _workspace(*, can_write: bool):
        return {
            'permissions': {
                'canWrite': can_write,
                'canImportLocal': False,
                'canBuildPreview': False,
            },
            'assetCount': 1,
            'profileCount': 1,
            'placementCount': 1,
            'unboundSlotCount': 0,
            'assetsUrl': '/admin/assets/',
            'assets': [
                {
                    'name': '灌装线模型',
                    'ext': '.glb',
                    'publicPath': '/assets/filler.glb',
                    'size': '2.0 MB',
                    'profileCount': 1,
                    'slotCount': 1,
                    'updatedAt': '2026-08-30 10:00',
                    'detailUrl': '/admin/assets/3d/1/',
                }
            ],
            'profiles': [
                {
                    'name': '首页设备预览',
                    'code': 'home-equipment',
                    'assetName': '灌装线模型',
                    'purposeLabel': '首页 3D 设备预览',
                    'background': '#ffffff',
                    'fov': 45,
                    'autoRotateLabel': '自动 / 20 秒',
                    'interactionLabel': '可拖拽',
                    'slotLabels': '首页设备预览',
                    'slotCount': 1,
                    'statusLabel': '启用',
                    'editUrl': '/admin/assets/3d/profiles/1/edit/' if can_write else '',
                }
            ],
            'placements': [
                {
                    'slotLabel': '首页设备预览',
                    'name': '首页模型槽位',
                    'slotCode': 'homepage_equipment_preview',
                    'profileName': '首页设备预览',
                    'profileCode': 'home-equipment',
                    'assetName': '灌装线模型',
                    'statusTone': 'green',
                    'statusLabel': '启用',
                    'updatedAt': '2026-08-30 10:00',
                    'editUrl': '/admin/assets/3d/placements/1/edit/' if can_write else '',
                    'profileEditUrl': '/admin/assets/3d/profiles/1/edit/' if can_write else '',
                }
            ],
            'checks': [],
            'previewGuidance': '由具备权限的成员生成预览产物。',
        }

    def test_read_only_3d_tables_remove_action_columns_instead_of_rendering_placeholders(self):
        html = get_template('console/_three_d_workspace.html').render(
            {'three_d_workspace': self._workspace(can_write=False)}
        )

        self.assertNotIn('three-d-grid-actions', html)
        self.assertNotIn('>只读<', html)

    def test_writable_3d_tables_keep_real_profile_and_placement_actions(self):
        html = get_template('console/_three_d_workspace.html').render(
            {'three_d_workspace': self._workspace(can_write=True)}
        )

        self.assertEqual(html.count('three-d-grid-actions'), 4)
        self.assertIn('编辑槽位', html)
        self.assertIn('>配置<', html)

    def test_1440_breakpoint_reflows_guidance_below_tables_before_they_overflow(self):
        css = (STATIC_ROOT / 'admin-shell.css').read_text(encoding='utf-8')
        desktop_reflow = css.split('@media (max-width: 1480px) {', 1)[1].split(
            '@media (max-width: 1280px) {',
            1,
        )[0]

        self.assertIn('.three-d-layout {', desktop_reflow)
        self.assertIn('grid-template-columns: minmax(0, 1fr)', desktop_reflow)
        self.assertIn('.three-d-layout > .three-d-side {', desktop_reflow)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr))', desktop_reflow)
        self.assertIn('.three-d-grid-table--profiles', css)
        self.assertIn('min-width: 940px', css)

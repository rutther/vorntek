from django.test import SimpleTestCase


class LoginUiTests(SimpleTestCase):
    def test_login_page_uses_branded_lead_workspace_ui(self):
        response = self.client.get('/admin/login/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '线索运营后台')
        self.assertContains(response, '员工登录')
        self.assertContains(response, '/static/console/vorntekLogo.png')
        self.assertContains(response, 'Vorntek')
        self.assertNotContains(response, 'New Crown')
        self.assertContains(response, 'console/admin-auth.css')
        self.assertNotContains(response, '企业网站控制平面')

"""Campaign registration must not be presented as candidate certification."""
import json
import unittest
from pathlib import Path

from vaa_build_v21 import register_content, status_banner

ROOT = Path(__file__).resolve().parents[1]


class CampaignStatusCopyTest(unittest.TestCase):
    def test_public_register_distinguishes_campaign_from_candidacy(self):
        register = json.loads((ROOT / 'vaa/vilnius_2027_register.json').read_text())
        data = json.loads((ROOT / 'vaa/vilnius_2027_v21.json').read_text())
        page = register_content(data, register, {'checks': []})
        self.assertIn('Registracija kampanijos dalyviu nepatvirtina kandidato ar kandidatų sąrašo registracijos rinkimams.', page)
        self.assertNotIn('oficialiai registruotas kandidatų sąrašas', page)
        self.assertNotIn('oficialiai užregistruotus Vilniaus miesto sąrašus', status_banner(data, register))

    def test_limits_do_not_deny_the_names_actually_in_the_snapshot(self):
        register = json.loads((ROOT / 'vaa/vilnius_2027_register.json').read_text())
        self.assertTrue(any(row['mayor_candidate'] for row in register['vilnius']['lists']))
        self.assertNotIn('Nė vienas kandidato vardas', ' '.join(register['limits']))

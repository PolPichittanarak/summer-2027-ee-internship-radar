import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracker import summer_ok, normalized_job, parse_greenhouse, parse_ashby, parse_lever, score_fit, detect_eligibility, detect_restrictions, stable_id, is_startup_watch

class RadarTests(unittest.TestCase):
    def test_strict_summer(self):
        self.assertTrue(summer_ok('Electrical Engineering Intern (Summer 2027)'))
        self.assertTrue(summer_ok('2027 Electrical Engineer Intern','We seek interns for Summer 2027.'))
        self.assertTrue(summer_ok('Hardware Engineering Intern', 'Work from May–August 2027 on PCB designs'))
        for title in ['Electrical Intern (Winter 2027)', 'Spring/Summer 2027 Hardware Intern', 'Summer 2026 ASIC Intern', 'FPGA Engineer New Grad Summer 2027']:
            self.assertFalse(summer_ok(title))
        self.assertFalse(summer_ok('Hardware Engineering Intern'))
        self.assertFalse(summer_ok('Marketing Intern (Summer 2027)'))
        self.assertTrue(summer_ok('Embedded Software Engineer Intern Summer 2027'))
        self.assertFalse(summer_ok('Backend Software Engineer Intern Summer 2027'))
    def test_resume_breadth(self):
        for title in ['Analog Design Intern Summer 2027', 'Mixed-signal Verification Intern Summer 2027', 'Power Electronics Intern Summer 2027', 'FPGA Intern Summer 2027', 'Photonics Intern Summer 2027']:
            self.assertTrue(summer_ok(title), title)
            self.assertGreater(score_fit(title, '')[0], 35)
    def test_normalize(self):
        row=normalized_job(dict(company='A',title='Hardware Intern Summer 2027',location='LA',url='https://site.org/jobs/1?x=2',description='PCB analog systems'), 'ATS', '2026-11-16')
        self.assertEqual(row['url'],'https://site.org/jobs/1')
        self.assertEqual(row['id'],stable_id('https://site.org/jobs/1?y=4'))
    def test_eligibility(self):
        self.assertEqual(detect_restrictions('US Citizenship or Green Card required')['citizenship_requirement'],'U.S.-person / protected status required')
    def test_sponsorship_and_clearance_are_separate(self):
        result=detect_restrictions('This position requires eligibility to obtain and maintain a U.S. Q security clearance. This includes U.S. Citizenship.')
        self.assertEqual(result['security_clearance'], 'Required / must be eligible')
        self.assertEqual(result['citizenship_requirement'], 'U.S. citizenship required')
        self.assertEqual(result['export_control'], 'Not stated')
        result=detect_restrictions('This position may require a security clearance.')
        self.assertEqual(result['security_clearance'], 'May be required')
        result=detect_restrictions('No security clearance required. U.S. citizenship is not required.')
        self.assertEqual(result['security_clearance'], 'Not required (stated)')
        self.assertEqual(result['citizenship_requirement'], 'Citizenship not required (stated)')
    def test_export_license_alternative(self):
        result=detect_restrictions('The person hired must be a U.S. person or otherwise eligible for a federally issued export control license. ITAR applies.')
        self.assertEqual(result['citizenship_requirement'], 'U.S. person OR export authorization')
        self.assertNotEqual(result['security_clearance'], 'Required / must be eligible')
        result=detect_restrictions('This position may require access to technology subject to EAR. Are you a US citizen or green card holder?')
        self.assertEqual(result['citizenship_requirement'], 'Not stated')
    def test_no_sponsorship(self):
        r=detect_restrictions('Due to export control rules, candidates must be U.S. persons. We are unable to sponsor visas.')
        self.assertEqual(r['visa_sponsorship'],'No sponsorship')
    def test_startup_watch(self):
        self.assertTrue(is_startup_watch('Antares'))
        self.assertTrue(is_startup_watch('Etched'))
        self.assertFalse(is_startup_watch('SpaceX'))
    def test_ats(self):
        gh=list(parse_greenhouse('Example','ex',json.dumps({'jobs':[{'id':123,'title':'EE Intern Summer 2027','absolute_url':'https://a.org/x','location':{'name':'LA'},'content':'analog design','first_published':'2026-11-01T12:00:00Z'}]})))
        self.assertEqual(gh[0]['location'],'LA')
        ash=list(parse_ashby('Example','ex',json.dumps({'jobs':[{'title':'FPGA Intern Summer 2027','location':'CA','jobUrl':'https://a.org/b','descriptionPlain':'Vivado RTL'}]})))
        self.assertEqual(ash[0]['title'],'FPGA Intern Summer 2027')
        lev=list(parse_lever('Example','ex',json.dumps([{'text':'Analog Intern Summer 2027','hostedUrl':'https://a.org/c','categories':{'location':'CA'},'createdAt':1790000000000}])))
        self.assertEqual(lev[0]['location'],'CA')

if __name__=='__main__': unittest.main()

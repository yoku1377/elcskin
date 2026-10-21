"""Markdown uses the established equal-round, all-valid-sample calculation."""
import csv
from pathlib import Path
import tempfile
import unittest
from consistency_report import generate_consistency_report
from folder_report import H3_COLUMNS


class MarkdownReportTests(unittest.TestCase):
    def test_two_unequal_length_rounds_are_equally_weighted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'input';source.mkdir()
            for i,values in enumerate(([1.,3.],[6.]),1):
                with (source/f'test_{i}.csv').open('w',newline='',encoding='utf8') as stream:
                    w=csv.writer(stream);w.writerow(['record_type',*H3_COLUMNS])
                    for v in values:w.writerow(['PAIRED',*([v]*8)])
            report=generate_consistency_report(source,root/'out',mode='folders_md')
            self.assertEqual(report['output_format'],'md');self.assertEqual(report['file_count'],2)
            result=report['reports'][0]
            for v in result['fs_max_pct']:self.assertAlmostEqual(v,2/30*100)
            for v in result['fs_mean_pct']:self.assertAlmostEqual(v,2/30*100)
            text=Path(result['path']).read_text(encoding='utf8')
            self.assertIn('不足10轮',text);self.assertIn('4.000000',text);self.assertIn('不重新套用',text)
            self.assertFalse(list((root/'out').rglob('*.xlsx')))


if __name__=='__main__':unittest.main()

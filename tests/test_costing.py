import unittest
from decimal import Decimal
from production import new_order, new_node, calculate_production
from costing import cost_document
from reports import document_html


class CostingTests(unittest.TestCase):
    def sample(self):
        order = new_order('001')
        part = new_node('part')
        part.update(name='<Сталь>', width=1000, height=1000, material=dict(name='Лист', width=1000, height=1000, price_sheet=122))
        order['nodes'] = [part]
        for s in order['stages']:
            s['enabled'] = s['kind'] == 'painting'
            if s['kind'] == 'painting':
                s['settings'].update(rate=60, paint_price=100)
        return calculate_production(order)

    def test_preserves_old_total_and_separates_paint(self):
        order = self.sample()
        doc = cost_document(order)
        self.assertEqual(doc['totals']['Всего']['cost'], Decimal(str(order['totals']['grand'])))
        self.assertEqual(doc['totals']['Всего']['sale'], doc['totals']['Всего']['cost'])
        self.assertTrue(any(r['name'] == 'Краска' and r['group'] == 'Материалы' for r in doc['lines']))
        self.assertEqual(doc['totals']['Работы']['cost'], Decimal('8'))

    def test_tax_and_separate_markups(self):
        order = self.sample()
        order['finance'] = dict(vat=22, material_markup=10, work_markup=20)
        doc = cost_document(order)
        steel = next(r for r in doc['lines'] if 'Лист' in r['name'])
        self.assertEqual(steel['net'], Decimal('100'))
        self.assertEqual(steel['sale'], Decimal('134.20'))
        for key in ('net', 'cost', 'sale'):
            self.assertEqual(doc['totals']['Всего'][key], sum(r[key] for r in doc['lines']))

    def test_documents_escaped_and_printable(self):
        for kind in ('report', 'receipt'):
            html = document_html(self.sample(), kind)
            self.assertIn('&lt;Сталь&gt;', html)
            self.assertIn('Сумма продажная с НДС', html)
            self.assertIn('Всего сумма заказа с НДС:', html)
            self.assertIn('A4 landscape', html)
            self.assertIn('table-header-group', html)

    def test_settings_reject_nonfinite_values(self):
        for value in ('nan', 'inf', -1):
            order = self.sample()
            order['finance'] = {'vat': value}
            with self.assertRaises(ValueError):
                cost_document(order)

    def test_multiple_products_no_invented_allocation(self):
        order = self.sample()
        order['nodes'].append(new_node('assembly'))
        doc = cost_document(calculate_production(order))
        self.assertEqual(len(doc['products']), 2)
        self.assertTrue(any('не распределена' in s for s in doc['notes']))


if __name__ == '__main__':
    unittest.main()

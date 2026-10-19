// VERCON supplies its application heading to the common report viewer.
import { openPdfReport as openSharedPdfReport } from '../reporting/pdf-preview.js';
import { reportHeading, createReportHeader } from './report-header.js';
export { downloadFile } from '../reporting/pdf-preview.js';

export function openPdfReport(title, data, owner, details = {}) {
  return openSharedPdfReport(title, data, owner, { ...details,
    heading: reportHeading(title, details), headingFor: value => reportHeading(value, details), createHeader: createReportHeader });
}

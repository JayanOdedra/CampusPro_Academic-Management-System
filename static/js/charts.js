/**
 * static/js/charts.js
 * -------------------
 * Reusable Chart.js helper functions used by all three dashboards.
 * Each function accepts a canvas element ID and a data object, then
 * renders the appropriate chart type.
 *
 * Why helpers instead of inline code?
 *   - DRY: bar charts for admin, teacher, and student all share one function.
 *   - Testable: each function has a single responsibility.
 *   - Readable: dashboard templates only call e.g. makeBarChart('id', data).
 */

'use strict';

/**
 * makeBarChart(canvasId, labels, values, label, color)
 * Renders a responsive vertical bar chart.
 *
 * @param {string}   canvasId  - The id of the <canvas> element.
 * @param {string[]} labels    - X-axis category labels.
 * @param {number[]} values    - Corresponding numeric values.
 * @param {string}   label     - Dataset label shown in the legend.
 * @param {string}   color     - RGBA fill colour for the bars.
 */
function makeBarChart(canvasId, labels, values, label, color) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;  // guard: canvas may not exist on every page

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: values,
        backgroundColor: color || 'rgba(54, 162, 235, 0.7)',
        borderColor:     color ? color.replace('0.7', '1') : 'rgba(54, 162, 235, 1)',
        borderWidth: 1,
        borderRadius: 4,   // rounded bar tops for a modern look
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true, position: 'top' },
      },
      scales: {
        y: {
          beginAtZero: true,
          // Cap the Y axis at 100 for percentage charts; caller can override
          max: undefined,
          ticks: { precision: 0 }
        }
      }
    }
  });
}

/**
 * makeBarChartCapped(canvasId, labels, values, label, color)
 * Same as makeBarChart but Y axis is capped at 100 (for percentage charts).
 */
function makeBarChartCapped(canvasId, labels, values, label, color) {
  // Accept either a string id or a canvas element directly
  const ctx = (typeof canvasId === 'string')
    ? document.getElementById(canvasId)
    : canvasId;
  if (!ctx) return;

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: values,
        backgroundColor: color || 'rgba(75, 192, 192, 0.7)',
        borderColor:     color ? color.replace('0.7', '1') : 'rgba(75, 192, 192, 1)',
        borderWidth: 1,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true, position: 'top' } },
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { precision: 1 } }
      }
    }
  });
}

/**
 * makeDoughnutChart(canvasId, labels, values, colors)
 * Renders a doughnut chart (e.g. approved vs pending users).
 *
 * @param {string}   canvasId - The id of the <canvas> element.
 * @param {string[]} labels   - Slice labels.
 * @param {number[]} values   - Slice sizes.
 * @param {string[]} colors   - Array of RGBA colour strings, one per slice.
 */
function makeDoughnutChart(canvasId, labels, values, colors) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;

  new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors || ['rgba(54,162,235,0.8)', 'rgba(255,99,132,0.8)'],
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true, position: 'bottom' },
      }
    }
  });
}

/**
 * makeLineChart(canvasId, labels, values, label)
 * Renders a line chart (e.g. student score trend over exams).
 */
function makeLineChart(canvasId, labels, values, label) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: label,
        data: values,
        fill: true,
        backgroundColor: 'rgba(153, 102, 255, 0.15)',
        borderColor:     'rgba(153, 102, 255, 1)',
        pointBackgroundColor: 'rgba(153, 102, 255, 1)',
        tension: 0.3,   // slight curve on the line
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true, position: 'top' } },
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { precision: 1 } }
      }
    }
  });
}

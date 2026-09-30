import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';

export interface BarChartItem {
  label: string;
  value: number;
}

@Component({
  selector: 'app-bar-chart',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './bar-chart.html',
  styleUrl: './bar-chart.css',
})
export class BarChartComponent {
  @Input() data: BarChartItem[] = [];
  @Input() unit = '';
  @Input() color = '#187696';
  @Input() emptyMessage = 'No data available';

  /** Optional shared prefix to strip from each label and show once instead, e.g. "Building" */
  @Input() labelPrefix = '';

  get maxValue(): number {
    const values = this.data.map(d => d.value);
    return values.length ? Math.max(...values) : 1;
  }

  barHeight(value: number): number {
    if (!this.maxValue) return 0;
    return Math.round((value / this.maxValue) * 100);
  }

  formatValue(value: number): string {
    const rounded = Math.round(value * 100) / 100;
    return rounded % 1 === 0 ? rounded.toString() : rounded.toFixed(2);
  }

  displayLabel(label: string): string {
    if (!this.labelPrefix) {
      return label;
    }
    const prefix = this.labelPrefix.trim();
    const trimmedLabel = label.trim();
    if (trimmedLabel.toLowerCase().startsWith(prefix.toLowerCase())) {
      return trimmedLabel.slice(prefix.length).trim();
    }
    return label;
  }
}
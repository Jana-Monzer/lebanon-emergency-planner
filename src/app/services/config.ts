import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class Config {
  private readonly http = inject(HttpClient);

  configurations: any;

  async loadConfigurations() {
    this.configurations = await firstValueFrom(this.http.get('configurations/config.json'));
  }
}
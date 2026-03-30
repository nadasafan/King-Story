import { CommonModule } from '@angular/common';
import { Component, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { distinctUntilChanged, filter, map } from 'rxjs/operators';
import { IStoryOrder } from '../../Core/Interface/istory-order';
import {
  isOrderLanguageEmpty,
  MSG_PDF_LANGUAGE_MISSING,
  resolvePdfLanguageFromOrder,
  StoryOrdersService
} from '../../Core/service/story-orders.service';
import Swal from 'sweetalert2';
import { SweetAlert } from '../../Core/service/sweet-alert';

type GeneratedAsset = {
  assetType: string;
  pageIndex: number | null;
  name?: string;
  path?: string;
  url: string;
  clientKey: string;
  sourceKey: string;
};

type GeneratedOrderDetails = IStoryOrder & {
  imagesFolder?: string;
  assets?: GeneratedAsset[];
};

type RegenerateResult = {
  assetId?: string;
  name?: string;
  pageIndex?: number | null;
  path?: string;
  url?: string;
  slidePath?: string;
  newSlidePath?: string;
  newSlideUrl?: string;
  oldSlidePath?: string;
  oldSlideUrl?: string;
};

@Component({
  selector: 'app-story-orders-generated-details',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './story-orders-generated-details.html',
  styleUrl: './story-orders-generated-details.scss'
})
export class StoryOrdersGeneratedDetails {
  order = signal<GeneratedOrderDetails | null>(null);
  isLoading = signal(false);
  isGeneratingPdf = signal(false);
  loadingAssetKey = signal<string | null>(null);
  loadingAction = signal<'regenerate' | 'confirm' | null>(null);
  pendingConfirmPaths = signal<Record<string, string>>({});

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private storyOrdersService: StoryOrdersService,
    private alert: SweetAlert
  ) {
    // إعادة تحميل الطلب عند تغيير :id في المسار — بدون هذا تبقى لغة/مجلد القصة السابقة (عربي) وتفسد PDF إنجليزي
    this.route.paramMap
      .pipe(
        map((p) => p.get('id')),
        filter((id): id is string => !!id?.trim()),
        distinctUntilChanged(),
        takeUntilDestroyed()
      )
      .subscribe((id) => this.loadOrder(id));
  }

  loadOrder(orderId: string) {
    this.isLoading.set(true);

    this.storyOrdersService.getGeneratedOne(orderId).subscribe({
      next: (res) => {
        const normalizedOrder = this.normalizeGeneratedOrder(res?.result ?? null);
        this.order.set(normalizedOrder);
        this.pendingConfirmPaths.set({});
        this.isLoading.set(false);
      },
      error: () => {
        this.isLoading.set(false);
      }
    });
  }

  private normalizeGeneratedOrder(
    apiOrder:
      | (IStoryOrder & {
          assets?: GeneratedAsset[];
          imagesFolder?: string;
          Language?: unknown;
          storyLanguage?: unknown;
          StoryLanguage?: unknown;
        })
      | null
      | undefined
  ): GeneratedOrderDetails | null {
    if (!apiOrder) return null;
    return {
      ...apiOrder,
      language:
        apiOrder.language ??
        apiOrder.Language ??
        apiOrder.storyLanguage ??
        apiOrder.StoryLanguage,
      assets: apiOrder.assets ? this.withClientKeys(apiOrder.assets) : []
    } as GeneratedOrderDetails;
  }

  getAssetImageUrl(asset: GeneratedAsset): string {
    return this.toAbsoluteUrl(asset.url);
  }

  imageAssets(): GeneratedAsset[] {
    return (this.order()?.assets ?? []).filter((asset) => asset.assetType === 'ImageDraft');
  }

  isAssetLoading(asset: GeneratedAsset): boolean {
    const key = this.loadingAssetKey();
    if (!key) return false;

    const slidePath = this.resolveSlidePath(asset);
    return key === asset.clientKey || key === slidePath || key === asset.url || key === asset.path;
  }

  isRegenerateLoading(asset: GeneratedAsset): boolean {
    return this.isAssetLoading(asset) && this.loadingAction() === 'regenerate';
  }

  isConfirmLoading(asset: GeneratedAsset): boolean {
    return this.isAssetLoading(asset) && this.loadingAction() === 'confirm';
  }

  hasPendingConfirm(asset: GeneratedAsset): boolean {
    return !!this.pendingConfirmPaths()?.[this.getSourceKey(asset)];
  }

  hasPendingConfirms(): boolean {
    return Object.keys(this.pendingConfirmPaths() ?? {}).length > 0;
  }

  async approveAllAndGeneratePdf() {
    const currentOrder = this.order();
    const imagesFolderDraft = currentOrder?.imagesFolder?.trim();
    let pdfLang = resolvePdfLanguageFromOrder(currentOrder);
    const userNameDraft = (currentOrder?.childFirstName ?? '').trim();

    if (!currentOrder || !imagesFolderDraft) {
      this.alert.toast('images_folder is missing', 'warning');
      return;
    }

    if (!userNameDraft) {
      this.alert.toast('user_name is missing', 'warning');
      return;
    }

    if (this.hasPendingConfirms()) {
      this.alert.toast('Confirm all pending slides first', 'warning');
      return;
    }

    /** إن اختارها المستخدم من السويت أليرت نثق بها؛ وإلا نستخدم لغة الطلب بعد إعادة الجلب من الخادم */
    let pdfLangOverride: 'en' | 'ar' | null = null;

    // لا يُكمل بدون لغة صالحة: إن لم تُرسل من الـ API نطلبها من المستخدم ونوقف PDF حتى الاختيار
    if (isOrderLanguageEmpty(currentOrder) || !pdfLang) {
      const { value: chosen, isDismissed } = await Swal.fire({
        icon: 'warning',
        title: 'نوع اللغة مفقود',
        html: `<p class="text-start mb-0">${MSG_PDF_LANGUAGE_MISSING}</p>`,
        input: 'select',
        inputOptions: {
          ar: 'العربية — ar',
          en: 'English — en'
        },
        inputPlaceholder: 'اختر لغة القصة',
        showCancelButton: true,
        confirmButtonText: 'متابعة لتوليد PDF',
        cancelButtonText: 'إلغاء',
        inputValidator: (v) => (v ? null : 'يجب اختيار العربية أو الإنجليزية')
      });

      if (isDismissed || (chosen !== 'ar' && chosen !== 'en')) {
        return;
      }
      pdfLangOverride = chosen;

      this.order.update((o) =>
        o
          ? ({
              ...o,
              language: chosen === 'ar' ? 0 : 1
            } as GeneratedOrderDetails)
          : o
      );
    }

    this.isGeneratingPdf.set(true);
    try {
      // إعادة جلب الطلب فور توليد PDF: بعد التنقل من قصة عربية لأخرى إنجليزية قد تبقى الواجهة بحالة قديمة
      const res = await firstValueFrom(this.storyOrdersService.getGeneratedOne(currentOrder.orderId));
      const fresh = this.normalizeGeneratedOrder(res?.result ?? null);

      const imagesFolder = fresh?.imagesFolder?.trim();
      if (!fresh || !imagesFolder) {
        this.alert.toast('images_folder is missing', 'warning');
        return;
      }

      const finalLang = pdfLangOverride ?? resolvePdfLanguageFromOrder(fresh);
      if (!finalLang) {
        this.alert.toast('اللغة مفقودة أو غير صالحة', 'warning');
        return;
      }

      const userName = (fresh.childFirstName ?? '').trim();
      if (!userName) {
        this.alert.toast('user_name is missing', 'warning');
        return;
      }

      this.order.set(fresh);

      await firstValueFrom(
        this.storyOrdersService.generatePdf(fresh.orderId, imagesFolder, finalLang, userName)
      );
      this.alert.toast('PDF generated successfully', 'success');
      void this.router.navigate(['/story-orders/pdfs']);
    } catch (err: unknown) {
      const ex = err as { error?: { message?: string }; message?: string } | undefined;
      this.alert.toast(ex?.error?.message || ex?.message || 'Failed to generate PDF', 'error');
    } finally {
      this.isGeneratingPdf.set(false);
    }
  }

  regenerate(asset: GeneratedAsset) {
    const currentOrder = this.order();
    const slidePath = this.resolveSlidePath(asset);
    const assetKey = this.getAssetKey(asset);
    const sourceKey = this.getSourceKey(asset);

    if (!currentOrder) return;
    if (!slidePath) {
      this.alert.toast('Slide path is missing', 'warning');
      return;
    }

    this.loadingAssetKey.set(assetKey);
    this.loadingAction.set('regenerate');

    this.storyOrdersService.regenerateSlide(currentOrder.orderId, slidePath).subscribe({
      next: (res) => {
        this.loadingAssetKey.set(null);
        this.loadingAction.set(null);

        const result = res?.result;
        const chosenPath =
          result?.newSlidePath ||
          result?.slidePath ||
          result?.path ||
          slidePath;

        const newAssetKey = this.appendAssetAfterRegenerate(asset, result);

        this.pendingConfirmPaths.update((prev) => {
          const next = { ...prev };
          next[sourceKey] = chosenPath;
          return next;
        });

        this.alert.toast('New version generated. Click Confirm on the version you want.', 'success');
      },
      error: (err) => {
        this.loadingAssetKey.set(null);
        this.loadingAction.set(null);
        this.alert.toast(err?.error?.message || 'Failed to regenerate slide', 'error');
      }
    });
  }

  confirm(asset: GeneratedAsset) {
    const currentOrder = this.order();
    if (!currentOrder) return;

    const slidePath = this.resolveSlidePath(asset);
    const assetKey = this.getAssetKey(asset);
    const sourceKey = this.getSourceKey(asset);
    const chosenPath = this.pendingConfirmPaths()?.[sourceKey] || slidePath;

    if (!chosenPath) {
      this.alert.toast('Slide path is missing', 'warning');
      return;
    }

    this.loadingAssetKey.set(assetKey);
    this.loadingAction.set('confirm');

    this.storyOrdersService.confirmSlide(currentOrder.orderId, chosenPath).subscribe({
      next: () => {
        this.loadingAssetKey.set(null);
        this.loadingAction.set(null);

        this.pendingConfirmPaths.update((prev) => {
          const next = { ...prev };
          delete next[sourceKey];
          return next;
        });

        this.alert.toast('Slide confirmed successfully', 'success');
        this.loadOrder(currentOrder.orderId);
      },
      error: (err) => {
        this.loadingAssetKey.set(null);
        this.loadingAction.set(null);
        this.alert.toast(err?.error?.message || 'Failed to confirm slide', 'error');
      }
    });
  }

  private resolveSlidePath(asset: GeneratedAsset): string {
    if (asset.path) return asset.path;

    if (!asset.url) return '';

    try {
      const parsedUrl = new URL(asset.url);
      const encodedPath = parsedUrl.searchParams.get('path');
      return encodedPath ? decodeURIComponent(encodedPath) : asset.url;
    } catch {
      return asset.url;
    }
  }

  private getAssetKey(asset: GeneratedAsset): string {
    return asset.clientKey;
  }

  private getSourceKey(asset: GeneratedAsset): string {
    return asset.sourceKey || asset.clientKey;
  }

  private withClientKeys(assets: Array<Omit<GeneratedAsset, 'clientKey' | 'sourceKey'> | GeneratedAsset>): GeneratedAsset[] {
    const seen = new Map<string, number>();

    return (assets ?? []).map((asset, index) => {
      const baseKey = (asset as GeneratedAsset).clientKey || asset.path || asset.url || `asset-${index}`;
      const count = (seen.get(baseKey) ?? 0) + 1;
      seen.set(baseKey, count);
      const uniqueKey = count > 1 ? `${baseKey}::${count}` : baseKey;

      return {
        ...asset,
        clientKey: uniqueKey,
        sourceKey: (asset as GeneratedAsset).sourceKey || uniqueKey
      } as GeneratedAsset;
    });
  }

  private appendAssetAfterRegenerate(asset: GeneratedAsset, result?: RegenerateResult): string {
    const nextPath = result?.newSlidePath || result?.slidePath || result?.path;
    const nextUrl = result?.newSlideUrl || result?.url;
    const nextName = result?.name || asset.name;
    const nextPageIndex = result?.pageIndex ?? asset.pageIndex;

    const baseKey = nextPath || nextUrl || asset.path || asset.url;
    const newClientKey = `${baseKey}::${Date.now()}`;

    const createdAsset: GeneratedAsset = {
      assetType: asset.assetType,
      pageIndex: nextPageIndex,
      name: nextName,
      path: nextPath,
      url: nextUrl || asset.url,
      clientKey: newClientKey,
      sourceKey: this.getSourceKey(asset)
    };

    this.order.update((current) => {
      if (!current) return current;

      const currentAssets = current.assets ?? [];
      const insertAfterIndex = currentAssets.findIndex((a) => a.clientKey === asset.clientKey);
      const nextAssets = [...currentAssets];

      if (insertAfterIndex >= 0) {
        nextAssets.splice(insertAfterIndex + 1, 0, createdAsset);
      } else {
        nextAssets.push(createdAsset);
      }

      return {
        ...current,
        assets: nextAssets
      };
    });

    return newClientKey;
  }

  private toAbsoluteUrl(url: string): string {
    if (!url) return '';
    if (url.startsWith('http')) return url;
    return `http://storyaigenerator.runasp.net${url}`;
  }
}

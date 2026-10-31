package app.shift.clinic;

import android.app.Activity;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.IOException;

/**
 * The clinic shift app in a WebView: the same pages as the browser, a trusted root certificate
 * inside the package, and the camera for receipt photos. The server decides everything else.
 */
public class MainActivity extends Activity {
    private static final int BACKGROUND = 0xFF0F172A;
    private static final int PRIMARY = 0xFF0F766E;
    private static final int REQUEST_FILE = 41;

    private SharedPreferences prefs;
    private WebView web;
    private LinearLayout setup;
    private LinearLayout failure;
    private EditText linkField;
    private TextView setupMessage;
    private String origin;
    private ValueCallback<Uri[]> chooser;
    private Uri cameraUri;
    private File cameraFile;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        prefs = getSharedPreferences("shift", Context.MODE_PRIVATE);
        origin = prefs.getString("origin", null);
        wipeOldPhotos();

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BACKGROUND);
        web = buildWebView();
        setup = buildSetup();
        failure = buildFailure();
        root.addView(web, match());
        root.addView(setup, match());
        root.addView(failure, match());
        setContentView(root);

        if (origin != null) {
            openApp(origin + "/?src=app");
        } else {
            showSetup(null);
        }
    }

    private static FrameLayout.LayoutParams match() {
        return new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }

    private WebView buildWebView() {
        WebView view = new WebView(this);
        view.setBackgroundColor(BACKGROUND);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setUserAgentString(settings.getUserAgentString() + " ShiftApp/0.4.0");
        CookieManager.getInstance().setAcceptCookie(true);
        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                return !sameOrigin(request.getUrl());
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                failure.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    failure.setVisibility(View.VISIBLE);
                }
            }
        });
        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback, FileChooserParams params) {
                return chooseFile(callback, params);
            }
        });
        return view;
    }

    private boolean sameOrigin(Uri uri) {
        return origin != null && originOf(uri).equals(origin);
    }

    private static String originOf(Uri uri) {
        String port = uri.getPort() > 0 ? ":" + uri.getPort() : "";
        return uri.getScheme() + "://" + uri.getHost() + port;
    }

    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        view.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        if (bold) {
            view.setTypeface(Typeface.DEFAULT_BOLD);
        }
        view.setPadding(0, 16, 0, 16);
        return view;
    }

    private Button button(String label, View.OnClickListener action) {
        Button view = new Button(this);
        view.setText(label);
        view.setTextSize(18);
        view.setTextColor(Color.WHITE);
        view.setBackgroundColor(PRIMARY);
        view.setMinHeight(132);
        view.setOnClickListener(action);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = 24;
        view.setLayoutParams(params);
        return view;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(BACKGROUND);
        layout.setGravity(Gravity.CENTER);
        layout.setPadding(48, 48, 48, 48);
        return layout;
    }

    private LinearLayout buildSetup() {
        LinearLayout layout = column();
        layout.addView(text("شیفت", 28, 0xFF2DD4BF, true));
        layout.addView(text("لینک ثبت دستگاه را که مدیر فرستاده اینجا بچسبانید.", 17, Color.WHITE, false));
        linkField = new EditText(this);
        linkField.setHint("https://…/enroll#t=…");
        linkField.setHintTextColor(0xFF94A3B8);
        linkField.setTextColor(Color.WHITE);
        linkField.setLayoutDirection(View.LAYOUT_DIRECTION_LTR);
        linkField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI
                | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        linkField.setBackgroundColor(0xFF1E293B);
        linkField.setPadding(32, 32, 32, 32);
        layout.addView(linkField);
        setupMessage = text("", 15, 0xFFFCA5A5, false);
        layout.addView(setupMessage);
        layout.addView(button("باز کردن", v -> submitLink()));
        return layout;
    }

    private LinearLayout buildFailure() {
        LinearLayout layout = column();
        layout.setVisibility(View.GONE);
        layout.addView(text("ارتباط با سرور درمانگاه برقرار نیست.", 20, Color.WHITE, true));
        layout.addView(text("به وای‌فای درمانگاه وصل باشید و دوباره امتحان کنید.", 16, 0xFFCBD5E1, false));
        layout.addView(button("تلاش دوباره", v -> {
            failure.setVisibility(View.GONE);
            web.reload();
        }));
        return layout;
    }

    private void showSetup(String message) {
        setupMessage.setText(message == null ? "" : message);
        setup.setVisibility(View.VISIBLE);
        web.setVisibility(View.INVISIBLE);
    }

    private void submitLink() {
        Uri link = Uri.parse(linkField.getText().toString().trim());
        if (!"https".equals(link.getScheme()) || link.getHost() == null) {
            setupMessage.setText("این نشانی درست نیست؛ باید با https شروع شود.");
            return;
        }
        origin = originOf(link);
        prefs.edit().putString("origin", origin).apply();
        openApp(link.toString());
    }

    private void openApp(String url) {
        setup.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(url);
    }

    private void wipeOldPhotos() {
        File[] files = PhotoProvider.dir(this).listFiles();
        if (files == null) {
            return;
        }
        long cutoff = System.currentTimeMillis() - 24L * 3600L * 1000L;
        for (File file : files) {
            if (file.lastModified() < cutoff) {
                file.delete();
            }
        }
    }

    private boolean chooseFile(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params) {
        if (chooser != null) {
            chooser.onReceiveValue(null);
        }
        chooser = callback;
        Intent chosen = Intent.createChooser(params.createIntent(), "عکس");
        try {
            cameraFile = File.createTempFile("photo", ".jpg", PhotoProvider.dir(this));
            cameraUri = Uri.parse("content://app.shift.clinic.files/" + cameraFile.getName());
            Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            camera.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
            camera.setClipData(ClipData.newRawUri("", cameraUri));
            camera.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            chosen.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[] {camera});
        } catch (IOException e) {
            cameraFile = null;
            cameraUri = null;
        }
        try {
            startActivityForResult(chosen, REQUEST_FILE);
        } catch (RuntimeException e) {
            chooser = null;
            callback.onReceiveValue(null);
            return false;
        }
        return true;
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != REQUEST_FILE || chooser == null) {
            return;
        }
        Uri[] answer = null;
        if (result == RESULT_OK) {
            boolean fromCamera = data == null || (data.getData() == null && data.getClipData() == null);
            if (fromCamera && cameraFile != null && cameraFile.length() > 0) {
                answer = new Uri[] {cameraUri};
            } else {
                answer = WebChromeClient.FileChooserParams.parseResult(result, data);
            }
        }
        chooser.onReceiveValue(answer);
        chooser = null;
    }

    @Override
    public void onBackPressed() {
        if (failure.getVisibility() != View.VISIBLE && web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        super.onPause();
    }
}

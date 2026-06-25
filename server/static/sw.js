self.addEventListener('push', function (event) {
  var data = event.data ? event.data.json() : {};
  if (data.web_push) return;
  var title = data.title || 'Stone Techno Companion';
  var options = {
    body: data.body || '',
    icon: '/favicon.png',
    badge: '/favicon.png',
    tag: data.tag || 'stc-notification',
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
  event.preventDefault();
  event.notification.close();
  var targetUrl =
    (event.notification.data && event.notification.data.url) ||
    event.notification.tag ||
    '/';
  var fullUrl = new URL(targetUrl, self.location.origin).href;

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (list) {
        for (var i = 0; i < list.length; i++) {
          if ('navigate' in list[i]) {
            return list[i].navigate(fullUrl).then(function (c) {
              return c.focus();
            });
          }
        }
        return self.clients.openWindow(fullUrl);
      }),
  );
});

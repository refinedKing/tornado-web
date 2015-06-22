#!-*-coding:utf-8-*-
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.gen
import os
import motor
import uuid
import time
import StringIO
import Image
from bson import ObjectId
from tornado.options import define, options
define("port", default=8000, help="run on the given port", type=int)
define("loadnum", default=2, help="pic number to load adt once", type=int)

class Application(tornado.web.Application):                        
    def __init__(self):
        handlers = [
                (r"/", MainHandler),
		(r"/register", RegHandler),
                (r"/login", LoginHandler),
                (r"/loadmore", LoadMoreHandler),
		(r"/upload",UploadFileHandler),
                (r"/ajax", NewPicNotifyHandler),
                (r"/addcomment", AddCommentHandler),
                (r"/getnewpic", GetNewPicHandler)]
        settings = dict(
                cookie_secret=
                "19N9ViOvRmmikCmFiW4ZBYgo17MZ6k+auNmdk+Aa18I=",
                login_url="/login",
                xsrf_cookies=True,
                debug=True,
                template_path=os.path.join(os.path.dirname(__file__),
                    "templates"),
                static_path=os.path.join(os.path.dirname(__file__),
                    "static"),
                ui_modules={"Picture": PictureModule}
                )
        self.db = motor.MotorClient().info
        tornado.web.Application.__init__(self, handlers, **settings)

class BaseHandler(tornado.web.RequestHandler):                     
    def get_current_user(self):
        return self.get_secure_cookie("user")

class MainHandler(BaseHandler):
    '''
    异步从数据库中读取图片路径, 初始加载的数目自定
    追踪用户点击加载更多的次数
    '''
    @tornado.gen.coroutine
    def get(self):
        db_pic = self.application.db.pic
        cursor = db_pic.find().sort([('_id', -1)]).limit(options.loadnum)
        self.piclist = []
        while (yield cursor.fetch_next):
            pic = cursor.next_object()
            src = {"picurl": pic["pic_path"] + pic["pic_name"],
                    "owner": pic["owner"], "_id": pic["_id"]}
            self.piclist.append(src)
        try:
            self.set_cookie("_id", str(pic["_id"]))
        finally:
            self.set_cookie("first_id", str(self.piclist[0]["_id"]))
            self.render("index.html", piclist=self.piclist)
        
class RegHandler(BaseHandler):
    '''
    注册界面, 基本数据库的增,查,写
    '''
    def get(self):
        self.error_render("")

    @tornado.gen.coroutine
    def post(self):
        db_user = self.application.db.user
        if  (yield db_user.find_one({"name": self.get_argument("name")})):
            self.error_render("用户名已注册")
        else:
            yield db_user.insert({"name": self.get_argument("name"),
                            "password": self.get_argument("password")})
            self.set_secure_cookie("user", self.get_argument("name"))
            self.redirect("/")            
    
    def error_render(self, warning):
        kwargs = dict(title= "用户注册", head= "请注册",
                 button_name= "注册", action= "register",
                 next= self.get_argument("next", "/"),
                 warning_message= warning, user="")
        self.render("login_reg.html", **kwargs)

class LoginHandler(BaseHandler):
    '''
    登录函数, 数据库的写入, 设置安全cooike, 30天失效
    '''
    def get(self):
        self.error_render(None, "")

    @tornado.gen.coroutine
    def post(self):
        db_user = self.application.db.user
        pwd = yield db_user.find_one({"name": self.get_argument("name")})
        try:
            if tornado.escape.utf8(self.get_argument("password")) \
                    ==pwd["password"]:
                self.set_secure_cookie("user", self.get_argument("name"))
                self.set_secure_cookie(
                               "password", self.get_argument("password"))
                self.redirect(self.get_argument("next", "/"))
                
            else:
                self.error_render("密码错误", self.get_argument("name"))
        except:
            self.error_render("用户不存在", "")
            #self.redirect("/register")

    def error_render(self, warning, user):
        kwargs = dict(title= "用户登录", head= "请登录", user= user,
                button_name= "登录", action= "login", 
                next= self.get_argument("next", "/"), 
                warning_message= warning)
        self.render("login_reg.html", **kwargs)

class UploadFileHandler(BaseHandler):
    '''图片上传handler,需要先登录'''
    @tornado.web.authenticated
    def get(self):
        self.render("upload.html")
    
    @tornado.web.authenticated
    def post(self):
        '''以时间戳+用户名方式存入数据库,防止同名覆盖'''
        f = self.request.files['file'][0]
        rawname = f['filename']
        suffix = f['filename'].split('.')[-1]
        name_part = self.get_secure_cookie("user")
        self.dstname = str(int(time.time())) + name_part + \
                '.' + rawname.split('.').pop()
        time_now = time.strftime('%Y_%m_%d',time.localtime(time.time()))
        self.path = "static/pic/" + time_now +"/"
        self.mkdir(self.path)
        if suffix.lower() in ('jpg', 'jpeg', 'gif', 'bmp', 'png'):
            img = Image.open(StringIO.StringIO(f['body']))
            img.save(self.path + self.dstname)
            self.save_to_db(self.dstname, self.path)
            NewPicNotifyHandler.send_message("new")
        else:
            self.write("<script>alert('图片格式错误!')</script>")
        self.redirect("/")

    def mkdir(self, path):
        '''
        新建目录函数,先检查存在性,通过接受传入的参数新建
        此处新建名为当天日期
        '''
        isExists = os.path.exists(path)
        if not isExists:
            os.makedirs(path)
    
    
    @tornado.gen.coroutine
    def save_to_db(self, pic_name, pic_path):
        '''
        存入数据库,包含图片名字,路径,所有者
        后期图片评论和点赞数通过update方法写入
        '''
        pic_db = self.application.db.pic
        dic = {"pic_name": pic_name, "pic_path": pic_path,
                "owner": self.get_secure_cookie("user")}
        pic_db.insert(dic)

class GetNewPicHandler(BaseHandler):
    '''
    获取新上传的图片
    '''
    @tornado.gen.coroutine
    def get(self):
        db_pic = self.application.db.pic
        first_id = ObjectId(self.get_cookie("first_id"))
        cursor = db_pic.find({'_id': {'$gt': first_id}}).sort([('_id', -1)])
        idlist = []
        while (yield cursor.fetch_next):
            pic = cursor.next_object()
            pic["picurl"] = pic["pic_path"] + pic["pic_name"]
            idlist.append(pic["_id"])
            string = self.render_string("modules/picture.html",
                    picture=pic)
            self.write(string)
        try:
            self.set_cookie("first_id", str(idlist[0]))
        finally:
            return

class LoadMoreHandler(BaseHandler):
    '''
    图片加载函数,一次加载options.loadnum张,
    如果最后加载张数不达,则全部加载
    具体方法为渲染div,第一次写n张
    第二次写2n张...
    '''
    @tornado.gen.coroutine
    def get(self):
        db_pic = self.application.db.pic
        cid = ObjectId(self.get_cookie("_id"))
        cursor = db_pic.find({'_id': {'$lt': cid}}).sort([('_id', -1)])     
        try:
            cursor = cursor[0: options.loadnum]
        except:
            cursor = cursor
        while (yield cursor.fetch_next):
            pic = cursor.next_object()
            pic["picurl"]=pic["pic_path"] + pic["pic_name"]
            self.set_cookie("_id", str(pic["_id"]))
            string = self.render_string("modules/picture.html", 
                                        picture=pic)
            self.write(string)

class NewPicNotifyHandler(BaseHandler):
    callbacks = set()
    users = set()

    @tornado.web.asynchronous
    def get(self):
        self.callbacks.add(self.on_new_message)
        self.user = self.get_cookie("user")
        if not self.user:
            self.user = str(uuid.uuid4())
            self.set_cookie("user", self.user)
        if self.user not in self.users:
            self.users.add(self.user)

    def on_new_message(self, message):
        if self.request.connection.stream.closed():
            return
        self.write(message)
        self.finish()         
    
    def on_connection_close(self):
        self.callbacks.remove(self.on_new_message)
        self.users.discard(self.user)
    
    @classmethod
    def send_message(cls, message):
        for callback in cls.callbacks:
            try:
                callback(message)
            finally:
                cls.callbacks = set()

class AddCommentHandler(BaseHandler):
    def post(self):
        user = self.get_secure_cookie("username")
        comment = self.get_argument("comment")
        time.sleep(0.5)
        self.write("<br />")
        self.write(comment)

class PictureModule(tornado.web.UIModule):
    '''
    ui渲染模块,渲染首页用
    '''
    def render(self, picture):
        return self.render_string("modules/picture.html", picture=picture)

def main():
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
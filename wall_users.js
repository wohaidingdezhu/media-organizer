// One shared username toolbar for the photo list/wall and movie poster wall.
function createWallUsers({host, selectedPaths, changed, locked = () => false}) {
  let data=null,busy=false,phase='',message='',filter='',assign='';
  let byPath=new Map(),byUnit=new Map(),chosen=new Set();
  const paths=()=>selectedPaths?selectedPaths():[...chosen].flatMap(id=>byUnit.get(id)?.files.map(file=>file.path)||[]);
  function selection(){
    const selected=paths(),ids=[...new Set(selected.map(path=>byPath.get(path)?.id).filter(Boolean))];
    return {ids,valid:!!data&&!busy&&!locked()&&ids.length>0&&ids.length<=200&&selected.every(path=>byPath.has(path))};
  }
  const render=mountWallUserControls(host,{
    filter:value=>{if(busy)return;filter=value;update();changed(true);},
    assign:value=>{if(busy||locked())return;assign=value;update();},
    save:async()=>{
      const current=selection();if(!current.valid)return;
      const user=assign,revision=data.revision;
      busy=true;phase='save';message='正在保存勾选项归属，请稍候…';update();changed();
      try{accept(await api({action:'assign',ids:current.ids,user_id:user,revision}));message=`已保存 ${current.ids.length} 项归属；旧整理计划需要重新预览。`;}
      catch(error){message='归属未保存：'+error.message+'；请刷新用户名后重试。';}
      finally{busy=false;phase='';update();changed();}
    },
    refresh:()=>load(),
    clear:()=>{if(busy||locked())return;chosen.clear();update();changed();},
  });
  function update(){
    const current=selection();
    render({ready:!!data,busy,phase,message,filter,assign,locked:locked(),canSave:current.valid,
      count:current.ids.length,users:data?.users||[],films:!selectedPaths});
  }
  async function api(payload){
    const response=await fetch('api/users',payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});
    const result=await response.json();if(!response.ok)throw Error(result.error||'用户分类读取失败');return result;
  }
  function choices(){
    const users=new Set((data?.users||[]).map(user=>user.id));
    if(!['','unassigned','mixed'].includes(filter)&&!users.has(filter))filter='';
    if(!users.has(assign))assign='';
  }
  function accept(result){
    data=result;byPath=new Map();byUnit=new Map();
    for(const unit of data.items){byUnit.set(unit.id,unit);for(const file of unit.files)byPath.set(file.path,unit);}
    chosen=new Set([...chosen].filter(id=>byUnit.has(id)));choices();
  }
  async function load(){
    if(busy)return;busy=true;phase='load';message='正在读取用户名，请稍候…';update();
    try{accept(await api());message='用户名已刷新；电影全部版本和分段一起归类。';}
    catch(error){data=null;byPath.clear();byUnit.clear();choices();message='用户分类不可用：'+error.message;}
    finally{busy=false;phase='';update();changed();}
  }
  update();
  return {
    load,update,busy:()=>busy,
    reset:()=>{filter='';update();},
    label:paths=>data?(byPath.get(paths[0])?.username||'归属未提供'):'用户名未加载',
    matches:paths=>!filter||(!!data&&byPath.get(paths[0])?.user_id===(filter==='unassigned'?'':filter)),
    checked:paths=>chosen.has(byPath.get(paths[0])?.id),
    toggle:(paths,value)=>{const unit=byPath.get(paths[0]);if(!unit||busy||locked())return;if(value)chosen.add(unit.id);else chosen.delete(unit.id);update();changed();},
    selectable:paths=>!!data&&!busy&&!locked()&&paths.every(path=>byPath.has(path)),
  };
}
if(typeof module!=='undefined'&&module.exports)module.exports={createWallUsers};
